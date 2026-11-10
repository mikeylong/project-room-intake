import copy
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import intake_store as store


def candidate(source_id="src_a", digest="a" * 64):
    value = {key: "" for key in store.LEGACY_FIELDS}
    value.update(source_id=source_id, source_type="local_file", root="/approved", path="/approved/" + source_id,
                 relative_path=source_id, name=source_id, size_bytes=8, relevance_score=0,
                 sha256=digest or "", hash_status="hashed" if digest else "not_requested", snippet_status="not_requested",
                 revision={"kind": "sha256", "value": digest} if digest else None,
                 metadata_token={"size_bytes": 8, "mtime_ns": 123}, provider=None, version=None,
                 retrieval_status="content_read" if digest else "metadata_only")
    return value


def payload(candidates=None, status="complete", roots=None):
    return {"schema_version": store.SCHEMA_VERSION, "scan_id": "test-scan", "generated_at": "2026-11-10T12:00:00Z",
            "roots": roots or ["/approved"], "query": "", "filters": {"include": [], "exclude": []},
            "content_access": {"mode": "hash", "hashes": True, "snippets": False},
            "coverage": {"status": status, "inputs": [], "errors": []},
            "enrichment": {"status": "complete", "errors": []}, "candidates": candidates if candidates is not None else [candidate()], "exclusions": []}


class ReviewStateTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir()).resolve()
        self.temporary = tempfile.TemporaryDirectory(prefix="intake-review-tests-", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.room = Path(self.temporary.name).resolve() / "room"

    def publish(self, candidates=None, **kwargs):
        return store.publish_room(self.room, payload(candidates, **kwargs))

    def current(self):
        return store.load_current(self.room)

    def approve(self, scope="snapshot", **kwargs):
        if scope == "snapshot" and "expected_revision" not in kwargs:
            observed = next(c for c in self.current()["candidates"] if c["source_id"] == "src_a")
            kwargs["expected_revision"] = copy.deepcopy(observed["revision"])
        return store.record_review(self.room, "src_a", "approved", scope, "Mike", **kwargs)

    def test_snapshot_lifecycle_does_not_transfer_approval(self):
        self.publish()
        self.approve(notes="Keep this evidence", classification="likely source")
        unchanged = self.publish([candidate(), candidate("src_b")])
        self.assertEqual(unchanged["candidates"][0]["review"]["status"], "approved")
        self.assertEqual(unchanged["candidates"][1]["review"]["status"], "unreviewed")
        changed = self.publish([candidate(digest="b" * 64)])["candidates"][0]
        self.assertEqual(changed["review"]["status"], "needs_review")
        self.assertEqual(changed["review"]["reason"], "revision_changed")
        uncertain = self.publish([candidate(digest=None)])["candidates"][0]
        self.assertEqual(uncertain["review"]["reason"], "revision_unknown")
        self.assertEqual(uncertain["review"]["notes"], "Keep this evidence")
        with self.assertRaisesRegex(ValueError, "verified revision"):
            self.approve()
        history = json.loads((self.room / "review_decisions.json").read_text())
        self.assertEqual(len(history["decisions"]), 1)
        self.assertEqual(history["decisions"][0]["revision"]["value"], "a" * 64)

    def test_source_scope_is_explicit_and_notes_survive_later_decisions(self):
        self.publish([candidate(digest=None)])
        self.approve(scope="source", notes="Scope explicitly covers this source", classification="context")
        changed = self.publish([candidate(digest="b" * 64)])["candidates"][0]
        self.assertEqual(changed["review"]["status"], "approved")
        store.record_review(self.room, "src_a", "deferred", "source", "Mike")
        history = json.loads((self.room / "review_decisions.json").read_text())["decisions"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["notes"], history[0]["notes"])
        self.assertEqual(history[1]["classification"], "context")
        self.assertEqual(self.current()["candidates"][0]["review"]["status"], "deferred")

    def test_missing_only_when_last_observed_scope_is_fully_scanned(self):
        self.publish()
        self.approve()
        partial = self.publish([], status="partial")["candidates"][0]
        self.assertEqual(partial["observation_status"], "unobserved")
        self.assertEqual(partial["review"]["status"], "unavailable")
        changed_scope = self.publish([], roots=["/other"])["candidates"][0]
        self.assertEqual(changed_scope["observation_status"], "unobserved")
        again = self.publish([], roots=["/other"])["candidates"][0]
        self.assertEqual(again["observation_status"], "unobserved")
        missing = self.publish([])["candidates"][0]
        self.assertEqual(missing["observation_status"], "missing")
        self.assertEqual(missing["revision"]["value"], "a" * 64)
        restored = self.publish()["candidates"][0]
        self.assertEqual(restored["review"]["status"], "approved")

    def test_empty_file_inputs_preserve_older_v2_scope_and_approval(self):
        legacy_v2 = payload()
        self.assertNotIn("files", legacy_v2)
        store.publish_room(self.room, legacy_v2)
        self.approve(notes="Existing reviewed room")
        history_before = (self.room / "review_decisions.json").read_bytes()
        repeated = payload()
        repeated.update(files=[], roots=["/approved", "/approved"])
        refreshed = store.publish_room(self.room, repeated)
        self.assertEqual(refreshed["candidates"][0]["review"]["status"], "approved")
        self.assertNotIn("files", refreshed["candidates"][0]["observation_scope"])
        repeated["candidates"] = []
        missing = store.publish_room(self.room, repeated)["candidates"][0]
        self.assertEqual(missing["observation_status"], "missing")
        self.assertEqual((self.room / "review_decisions.json").read_bytes(), history_before)

    def test_exact_file_scope_changes_preserve_historical_provenance(self):
        first = payload()
        first.update(roots=[], files=["/approved/src_a", "/approved/src_a"])
        provenance = [{"kind": "file", "path": "/approved/src_a"}] * 2
        first["candidates"][0]["discovered_via"] = provenance
        initial = store.publish_room(self.room, first)
        self.approve()
        self.assertEqual(initial["candidates"][0]["discovered_via"], provenance)
        with (self.room / "candidate_sources.csv").open(newline="") as handle:
            self.assertEqual(json.loads(next(csv.DictReader(handle))["discovered_via"]), provenance)
        changed_scope = payload([])
        changed_scope.update(roots=[], files=["/approved/src_b"])
        unseen = store.publish_room(self.room, changed_scope)["candidates"][0]
        self.assertEqual(unseen["observation_status"], "unobserved")
        self.assertEqual(unseen["discovered_via"], provenance)
        self.assertEqual(unseen["observation_scope"]["files"], ["/approved/src_a"])
        first["candidates"] = []
        missing = store.publish_room(self.room, first)["candidates"][0]
        self.assertEqual(missing["observation_status"], "missing")
        self.assertEqual(missing["discovered_via"], provenance)

    def test_review_requires_observed_identity_and_explicit_scope(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, "currently observed"):
            store.record_review(self.room, "unknown", "approved", "source", "Mike")
        self.publish([])
        with self.assertRaisesRegex(ValueError, "currently observed"):
            self.approve()

    def test_invalid_current_revision_cannot_append_review_history(self):
        self.publish()
        review_path = self.room / "review_decisions.json"
        history_before = review_path.read_bytes()
        current = self.current()
        current["candidates"][0]["revision"]["value"] = "b" * 64
        (self.room / "candidate_sources.json").write_text(json.dumps(current))
        with self.assertRaisesRegex(ValueError, "must match"):
            self.approve()
        self.assertEqual(review_path.read_bytes(), history_before)
        self.assertEqual(len(json.loads(review_path.read_text())["decisions"]), 0)

    def test_stale_snapshot_decision_cannot_approve_a_new_revision(self):
        observed_a = self.publish()["candidates"][0]["revision"]
        self.publish([candidate(digest="b" * 64)])
        review_path = self.room / "review_decisions.json"
        history_before = review_path.read_bytes()
        view_before = {name: (self.room / name).read_bytes() for name in store.ARTIFACTS}
        with self.assertRaisesRegex(ValueError, "differs from the expected"):
            self.approve(expected_revision=observed_a)
        self.assertEqual(review_path.read_bytes(), history_before)
        self.assertEqual(view_before, {name: (self.room / name).read_bytes() for name in store.ARTIFACTS})
        current_b = self.current()["candidates"][0]["revision"]
        approved = self.approve(expected_revision=current_b)
        self.assertEqual(approved["revision"], current_b)
        self.assertEqual(self.current()["candidates"][0]["review"]["status"], "approved")

    def test_expected_revision_is_required_only_for_snapshot_scope(self):
        observed = self.publish()["candidates"][0]["revision"]
        review_path = self.room / "review_decisions.json"
        history_before = review_path.read_bytes()
        view_before = {name: (self.room / name).read_bytes() for name in store.ARTIFACTS}
        with self.assertRaisesRegex(ValueError, "expected verified revision"):
            store.record_review(self.room, "src_a", "approved", "snapshot", "Mike")
        with self.assertRaisesRegex(ValueError, "only valid for snapshot"):
            self.approve(scope="source", expected_revision=observed)
        self.assertEqual(review_path.read_bytes(), history_before)
        self.assertEqual(view_before, {name: (self.room / name).read_bytes() for name in store.ARTIFACTS})

    def test_interrupted_publish_keeps_all_canonical_outputs_on_old_generation(self):
        first = self.publish()
        old = {name: (self.room / name).read_bytes() for name in store.ARTIFACTS}
        with mock.patch.object(store, "_switch_current", side_effect=OSError("injected commit failure")):
            with self.assertRaises(OSError):
                self.publish([candidate(digest="b" * 64)])
        self.assertEqual(old, {name: (self.room / name).read_bytes() for name in store.ARTIFACTS})
        self.assertEqual(self.current()["publication"], first["publication"])
        second = self.publish([candidate(digest="b" * 64)])
        self.assertNotEqual(second["publication"], first["publication"])
        self.assertTrue((self.room / ".intake/generations" / first["publication"]["generation_id"]).is_dir())

    def test_review_history_survives_view_refresh_failure(self):
        self.publish()
        with mock.patch.object(store, "_switch_current", side_effect=OSError("injected refresh failure")):
            with self.assertRaises(OSError):
                self.approve(notes="Durable before view refresh")
        history = json.loads((self.room / "review_decisions.json").read_text())
        self.assertEqual(len(history["decisions"]), 1)
        self.assertEqual(self.current()["review_history_count"], 0)
        refreshed = self.publish()
        self.assertEqual(refreshed["review_history_count"], 1)
        self.assertEqual(refreshed["candidates"][0]["review"]["status"], "approved")

    def test_first_publication_failure_is_retryable(self):
        with mock.patch.object(store, "_switch_current", side_effect=OSError("injected first commit failure")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertIsNone(self.current())
        self.assertTrue((self.room / "candidate_sources.json").is_symlink())
        self.publish()
        self.assertEqual(self.current()["schema_version"], store.SCHEMA_VERSION)

    def test_room_ancestor_and_lock_symlinks_are_rejected(self):
        target = self.room.parent / "target"
        target.mkdir()
        alias = self.room.parent / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            store.publish_room(alias / "new-room", payload())
        self.assertFalse((target / "new-room").exists())
        self.room.mkdir()
        (self.room / ".intake").mkdir()
        untouched = self.room.parent / "untouched"
        untouched.write_text("must not be a lock")
        (self.room / ".intake/room.lock").symlink_to(untouched)
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual(untouched.read_text(), "must not be a lock")

    def test_extensions_survive_refresh_and_conflicts_do_not_overwrite(self):
        initial = payload()
        initial["extensions"] = {"example.room": {"note": "keep"}}
        initial["candidates"][0]["extensions"] = {"example.source": {"classification": "context"}}
        store.publish_room(self.room, initial)
        refreshed = self.publish()
        self.assertEqual(refreshed["extensions"], initial["extensions"])
        self.assertEqual(refreshed["candidates"][0]["extensions"], initial["candidates"][0]["extensions"])
        conflicting = payload()
        conflicting["extensions"] = {"example.room": {"note": "replace"}}
        with self.assertRaisesRegex(ValueError, "Conflicting extension"):
            store.publish_room(self.room, conflicting)
        self.assertEqual(self.current()["publication"], refreshed["publication"])

    def test_unknown_connector_metadata_remains_unknown(self):
        source = candidate(digest=None)
        source.update(source_type="connector", provider="example", path="example://object/1", version="etag-1",
                      size_bytes=None, modified_at=None, owner=None, mode=None, metadata_token=None,
                      retrieval_status="not_retrieved")
        published = self.publish([source])
        self.assertIsNone(published["candidates"][0]["size_bytes"])
        self.assertIsNone(published["candidates"][0]["revision"])
        with self.assertRaisesRegex(ValueError, "verified revision"):
            self.approve()
        self.publish([], status="partial")

    def test_schema_required_fields_and_status_vocabularies_match_runtime(self):
        schema = json.loads((Path(store.__file__).parents[1] / "references/schemas/candidates.v2.schema.json").read_text())
        candidate_schema = schema["properties"]["candidates"]["items"]
        for field, expected in (("hash_status", store.HASH_STATUSES), ("snippet_status", store.SNIPPET_STATUSES), ("retrieval_status", store.RETRIEVAL_STATUSES)):
            self.assertEqual(set(candidate_schema["properties"][field]["enum"]), expected)
        for field in candidate_schema["required"]:
            incomplete = payload()
            del incomplete["candidates"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                store.validate_payload(incomplete)
        invalid = payload()
        invalid["coverage"]["status"] = []
        with self.assertRaises(ValueError):
            store.validate_payload(invalid)
        published = self.publish()
        with (self.room / "candidate_sources.csv").open(newline="") as handle:
            exported = next(csv.DictReader(handle))
        self.assertEqual(exported["scan_id"], published["scan_id"])
        self.assertEqual(exported["generation_id"], published["publication"]["generation_id"])
        self.assertEqual(exported["coverage_status"], published["coverage"]["status"])

    def write_legacy(self):
        self.room.mkdir()
        legacy_candidate = {key: str(candidate()[key]) for key in store.LEGACY_FIELDS}
        legacy = {"schema_version": "project-room-intake.candidates.v1", "generated_at": "2026-11-09T00:00:00Z",
                  "roots": ["/approved"], "query": "", "candidates": [legacy_candidate], "exclusions": []}
        (self.room / "candidate_sources.json").write_text(json.dumps(legacy))
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=store.LEGACY_FIELDS)
        writer.writeheader()
        writer.writerow(legacy_candidate)
        (self.room / "candidate_sources.csv").write_text(buffer.getvalue())
        (self.room / "scan_summary.md").write_text("# Existing summary\nHuman note preserved in backup.\n")
        return legacy

    def test_vanilla_legacy_conversion_keeps_exact_backup(self):
        self.write_legacy()
        originals = {name: (self.room / name).read_bytes() for name in store.ARTIFACTS}
        updated = self.publish()
        self.assertEqual(updated["schema_version"], store.SCHEMA_VERSION)
        self.assertEqual(originals, {name: (self.room / ".intake/legacy-v1" / name).read_bytes() for name in store.ARTIFACTS})
        self.assertTrue(all((self.room / name).is_symlink() for name in store.ARTIFACTS))

    def test_retained_legacy_sources_validate_without_claiming_current_coverage(self):
        self.write_legacy()
        updated = self.publish([])
        store.validate_payload(updated)
        retained = updated["candidates"][0]
        self.assertEqual(retained["observation_status"], "unobserved")
        self.assertEqual(retained["retrieval_status"], "content_read")
        self.assertIsNone(retained["revision"])

    def test_legacy_csv_review_edit_refuses_without_overwrite(self):
        self.write_legacy()
        csv_path = self.room / "candidate_sources.csv"
        csv_path.write_text(csv_path.read_text().replace("src_a", "human-edited", 1))
        originals = {name: (self.room / name).read_bytes() for name in store.ARTIFACTS}
        with self.assertRaisesRegex(ValueError, "CSV differs"):
            self.publish()
        self.assertEqual(originals, {name: (self.room / name).read_bytes() for name in store.ARTIFACTS})
        self.assertFalse((self.room / ".intake/current").exists())

    def test_legacy_unknown_extension_refuses_without_overwrite(self):
        legacy = self.write_legacy()
        legacy["reviewed"] = True
        path = self.room / "candidate_sources.json"
        path.write_text(json.dumps(legacy))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "extensions"):
            self.publish()
        self.assertEqual(path.read_bytes(), before)

    def test_cli_concurrent_reviews_preserve_both_history_entries(self):
        observed = self.publish()["candidates"][0]["revision"]
        cli = str(Path(store.__file__).with_name("review_sources.py"))
        processes = [subprocess.Popen([sys.executable, cli, "--room", str(self.room), "--source-id", "src_a",
                                      "--decision", "approved", "--scope", "snapshot", "--actor", actor,
                                      "--expected-sha256", observed["value"]],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for actor in ("Reviewer one", "Reviewer two")]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
        reviews = json.loads((self.room / "review_decisions.json").read_text())["decisions"]
        self.assertEqual({r["actor"] for r in reviews}, {"Reviewer one", "Reviewer two"})
        self.assertEqual(self.current()["review_history_count"], 2)

    def test_cli_rejects_missing_stale_and_irrelevant_snapshot_tokens(self):
        observed_a = self.publish()["candidates"][0]["revision"]
        self.publish([candidate(digest="b" * 64)])
        review_path = self.room / "review_decisions.json"
        history_before = review_path.read_bytes()
        view_before = {name: (self.room / name).read_bytes() for name in store.ARTIFACTS}
        cli = str(Path(store.__file__).with_name("review_sources.py"))
        command = [sys.executable, cli, "--room", str(self.room), "--source-id", "src_a", "--decision", "approved", "--actor", "Mike"]
        scenarios = [(["--scope", "snapshot"], 2, "required"),
                     (["--scope", "snapshot", "--expected-sha256", observed_a["value"]], 1, "differs from the expected"),
                     (["--scope", "source", "--expected-sha256", observed_a["value"]], 2, "only valid")]
        for arguments, expected_exit, message in scenarios:
            with self.subTest(arguments=arguments):
                result = subprocess.run(command + arguments, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected_exit, result.stderr)
                self.assertIn(message, result.stderr)
        self.assertEqual(review_path.read_bytes(), history_before)
        self.assertEqual(view_before, {name: (self.room / name).read_bytes() for name in store.ARTIFACTS})
        matched = subprocess.run(command + ["--scope", "snapshot", "--expected-sha256", "b" * 64], capture_output=True, text=True, timeout=10)
        self.assertEqual(matched.returncode, 0, matched.stderr)
        self.assertEqual(json.loads(matched.stdout)["revision"], {"kind": "sha256", "value": "b" * 64})


if __name__ == "__main__":
    unittest.main()
