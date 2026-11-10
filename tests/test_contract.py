"""Contract examples and refusal tests, without a schema-validator dependency."""
import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import intake_store as store


EXAMPLES = json.loads((Path(__file__).parent / "fixtures/contracts/candidates.json").read_text())


class ContractTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir()).resolve()
        self.temporary = tempfile.TemporaryDirectory(prefix="intake-contract-tests-", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()

    def test_local_symlink_connector_and_partial_examples_publish(self):
        self.assertEqual(set(EXAMPLES), {"local_file", "local_symlink", "connector_not_retrieved", "connector_retrieved", "partial_discovery"})
        for name, example in EXAMPLES.items():
            with self.subTest(example=name):
                original = copy.deepcopy(example)
                store.validate_payload(example)
                published = store.publish_room(self.base / name, example)
                store.validate_payload(published)
                self.assertEqual(example, original, "publishing must not mutate the caller's manifest")
                self.assertEqual(published["coverage"], original["coverage"])
                for expected, actual in zip(original["candidates"], published["candidates"]):
                    for field in expected:
                        self.assertEqual(actual[field], expected[field])
                    self.assertEqual(actual["review"]["status"], "unreviewed")

    def test_provider_version_does_not_substitute_for_verified_content_revision(self):
        room = self.base / "provider"
        published = store.publish_room(room, EXAMPLES["connector_not_retrieved"])
        source = published["candidates"][0]
        self.assertEqual(source["version"], "etag-fixture-1")
        self.assertIsNone(source["size_bytes"])
        self.assertIsNone(source["modified_at"])
        self.assertIsNone(source["revision"])
        before = (room / "review_decisions.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "verified revision"):
            store.record_review(room, source["source_id"], "approved", "snapshot", "Fixture reviewer",
                                expected_revision={"kind": "sha256", "value": source["version"]})
        self.assertEqual((room / "review_decisions.json").read_bytes(), before)

    def test_malformed_nested_contracts_do_not_replace_reviewed_room(self):
        room = self.base / "reviewed"
        initial = store.publish_room(room, EXAMPLES["local_file"])
        store.record_review(room, initial["candidates"][0]["source_id"], "approved", "snapshot", "Fixture reviewer",
                            expected_revision=initial["candidates"][0]["revision"])
        before = {name: (room / name).read_bytes() for name in (*store.ARTIFACTS, "review_decisions.json")}
        invalid_cases = []

        def invalid(name, change):
            value = copy.deepcopy(EXAMPLES["local_file"])
            change(value)
            invalid_cases.append((name, value))

        invalid("relative exact file", lambda p: p.update(files=["unapproved-relative.txt"]))
        invalid("boolean input count", lambda p: p["coverage"]["inputs"][0].update(inspected_count=True))
        invalid("missing input path", lambda p: p["coverage"]["inputs"][0].pop("path"))
        invalid("unknown input kind", lambda p: p["coverage"]["inputs"][0].update(kind="directory"))
        invalid("undeclared input", lambda p: p["coverage"]["inputs"][0].update(root="/elsewhere", path="/elsewhere"))
        invalid("malformed error", lambda p: p["enrichment"]["errors"].append({"path": [], "operation":"read", "error":"OSError", "message":"fixture"}))
        invalid("malformed exclusion", lambda p: p["exclusions"].append({"root":"/fixtures/local", "path":"/fixtures/local/x", "relative_path":"x", "reason":False, "kind":"file"}))
        invalid("invalid publication", lambda p: p.update(publication={"generation_id": [], "published_at":"now"}))
        invalid("metadata token extension", lambda p: p["candidates"][0]["metadata_token"].update(reviewed=True))
        invalid("review status", lambda p: p["candidates"][0].update(review={"status":"trusted", "reason":"no_decision", "decision_id":None}))
        invalid("empty provenance", lambda p: p["candidates"][0].update(discovered_via=[]))
        invalid("undeclared provenance", lambda p: p["candidates"][0].update(discovered_via=[{"kind":"root", "path":"/elsewhere"}]))
        invalid("provenance hidden by parent traversal", lambda p: p["candidates"][0].update(discovered_via=[{"kind":"root", "path":"/fixtures/../elsewhere"}]))
        invalid("invalid observation scope", lambda p: p["candidates"][0].update(observation_scope={"roots":True,"filters":{"include":[],"exclude":[]}}))
        for name, value in invalid_cases:
            with self.subTest(case=name), self.assertRaises(ValueError):
                store.publish_room(room, value)
            self.assertEqual(before, {name: (room / name).read_bytes() for name in before})

    def test_repeated_overlap_provenance_survives_json_and_csv(self):
        example = copy.deepcopy(EXAMPLES["local_file"])
        example["roots"] *= 2
        source_path = example["candidates"][0]["path"]
        example["files"] = [source_path, source_path]
        example["coverage"]["inputs"] *= 2
        file_outcome = {"root":source_path,"path":source_path,"kind":"file","status":"complete","inspected_count":1,"error_count":0,"exclusion_count":0}
        example["coverage"]["inputs"] += [file_outcome, file_outcome]
        provenance = [{"kind":"root","path":"/fixtures/local"}] * 2 + [{"kind":"file","path":source_path}] * 2
        example["candidates"][0]["discovered_via"] = provenance
        published = store.publish_room(self.base / "overlap", example)
        self.assertEqual(len(published["candidates"]), 1)
        self.assertEqual(published["roots"], example["roots"])
        self.assertEqual(published["files"], example["files"])
        self.assertEqual(published["candidates"][0]["discovered_via"], provenance)
        self.assertEqual(published["candidates"][0]["observation_scope"], {"roots":["/fixtures/local"],"files":[source_path],"filters":{"include":[],"exclude":[]}})
        with (self.base / "overlap/candidate_sources.csv").open(newline="") as handle:
            self.assertEqual(json.loads(next(csv.DictReader(handle))["discovered_via"]), provenance)

    def test_schema_keeps_new_fields_optional_for_older_v2(self):
        schema = json.loads((Path(store.__file__).parents[1] / "references/schemas/candidates.v2.schema.json").read_text())
        self.assertNotIn("files", schema["required"])
        self.assertNotIn("discovered_via", schema["properties"]["candidates"]["items"]["required"])
        legacy_v2 = copy.deepcopy(EXAMPLES["local_file"])
        legacy_v2.pop("files")
        legacy_v2["candidates"][0].pop("discovered_via")
        for outcome in legacy_v2["coverage"]["inputs"]:
            outcome.pop("kind")
            outcome.pop("path")
        store.validate_payload(legacy_v2)
        published = store.publish_room(self.base / "older-v2", legacy_v2)
        self.assertNotIn("discovered_via", published["candidates"][0])


if __name__ == "__main__":
    unittest.main()
