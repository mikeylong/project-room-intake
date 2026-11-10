"""Observable scoring, binding, and refusal behavior for bounded trial records."""

import contextlib
import copy
import hashlib
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import score_trial as scorer

PROTOCOL = json.loads((Path(__file__).parent / "fixtures/trials/protocol.json").read_text())


class TrialTests(unittest.TestCase):
    def setUp(self):
        root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir()).resolve()
        self.temporary = tempfile.TemporaryDirectory(prefix="intake-trial-tests-", dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.frozen = scorer.freeze_protocol(copy.deepcopy(PROTOCOL))

    def observation(self, arm, candidates=None, used=None, **measurements):
        result = {
            "schema_version": scorer.OBSERVATION_VERSION,
            "trial_id": PROTOCOL["trial_id"], "protocol_sha256": self.frozen["protocol_sha256"],
            "arm_id": arm, "candidate_source_ids": candidates or [], "used_sources": used or [],
            "user_corrections": None, "clarification_turns": None, "elapsed_seconds": None,
            "human_effort_minutes": None, "notes": ["Synthetic observation, not a real trial."],
        }
        result.update(measurements)
        return result

    def valid_observations(self):
        return [self.observation("ordinary"), self.observation("skill")]

    def write(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def run_main(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return scorer.main(list(map(str, arguments)))

    def test_hand_computed_counts_and_deltas_preserve_arm_order(self):
        first = self.observation("ordinary", ["important-a", "context", "irrelevant"], [
            {"source_id": "important-a", "sha256": "f" * 64},
            {"source_id": "context", "sha256": None},
        ], user_corrections=2, clarification_turns=3, elapsed_seconds=100.5, human_effort_minutes=7)
        second = self.observation("skill", ["important-a", "important-b"], [
            {"source_id": "important-a", "sha256": "a" * 64},
            {"source_id": "important-b", "sha256": "b" * 64},
        ], user_corrections=1, clarification_turns=4, elapsed_seconds=130.5, human_effort_minutes=5)
        original = copy.deepcopy([first, second])
        result = scorer.score_trial(self.frozen, [second, first])
        self.assertEqual([arm["arm_id"] for arm in result["arms"]], ["ordinary", "skill"])
        metrics = result["arms"][0]["metrics"]
        for field, source in (("missed_important_sources", "important-b"), ("irrelevant_candidates", "irrelevant"), ("version_mistakes", "important-a"), ("unverified_versions", "context")):
            self.assertEqual(metrics[field], {"count": 1, "source_ids": [source]})
            self.assertEqual(result["arms"][1]["metrics"][field]["count"], 0)
        self.assertEqual(result["deltas"], {"missed_important_sources": -1, "irrelevant_candidates": -1, "version_mistakes": -1, "unverified_versions": -1, "user_corrections": -1, "clarification_turns": 1, "elapsed_seconds": 30.0, "human_effort_minutes": -2})
        self.assertEqual(result["confounders"], PROTOCOL["confounders"])
        self.assertEqual(result["measurement_notes"], PROTOCOL["measurement_notes"])
        self.assertEqual([first, second], original)

    def test_unknown_effort_is_not_imputed_as_zero(self):
        observations = self.valid_observations()
        observations[1].update(user_corrections=0, clarification_turns=0, elapsed_seconds=0, human_effort_minutes=0)
        result = scorer.score_trial(self.frozen, observations)
        for field in scorer.SCALAR_METRICS:
            self.assertIsNone(result["arms"][0]["metrics"][field])
            self.assertEqual(result["arms"][1]["metrics"][field], 0)
            self.assertIsNone(result["deltas"][field])

    def test_unknown_version_is_reported_separately_from_wrong_version(self):
        observations = self.valid_observations()
        observations[0].update(candidate_source_ids=["important-a"], used_sources=[{"source_id": "important-a", "sha256": None}])
        metrics = scorer.score_trial(self.frozen, observations)["arms"][0]["metrics"]
        self.assertEqual(metrics["unverified_versions"]["source_ids"], ["important-a"])
        self.assertEqual(metrics["version_mistakes"]["count"], 0)
        self.assertEqual(metrics["missed_important_sources"]["source_ids"], ["important-b"])

    def test_freeze_hash_ignores_object_key_order_but_detects_changed_criteria(self):
        reversed_keys = dict(reversed(list(PROTOCOL.items())))
        self.assertEqual(scorer.freeze_protocol(reversed_keys), self.frozen)
        encoded = json.dumps(PROTOCOL, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        self.assertEqual(self.frozen["protocol_sha256"], hashlib.sha256(encoded).hexdigest())
        tampered = copy.deepcopy(self.frozen)
        tampered["protocol"]["sources"][0]["important"] = False
        with self.assertRaisesRegex(ValueError, "digest"):
            scorer.score_trial(tampered, self.valid_observations())

    def test_observation_must_bind_to_original_trial_and_digest(self):
        for field, replacement in (("trial_id", "another-trial"), ("protocol_sha256", "e" * 64)):
            observations = self.valid_observations()
            observations[0][field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                scorer.score_trial(self.frozen, observations)

    def test_duplicate_missing_and_unknown_arms_are_rejected(self):
        variants = [[], [self.observation("ordinary")], [self.observation("ordinary")] * 2, [self.observation("ordinary"), self.observation("unknown")]]
        for observations in variants:
            with self.subTest(arms=[o["arm_id"] for o in observations]), self.assertRaises(ValueError):
                scorer.score_trial(self.frozen, observations)

    def test_malformed_protocols_are_rejected_before_freeze(self):
        changes = [
            lambda p: p["sources"].append(copy.deepcopy(p["sources"][0])),
            lambda p: p["sources"][0].update(important=1),
            lambda p: p["sources"][0].update(relevant=False),
            lambda p: p["sources"][0].update(sha256="provider-etag"),
            lambda p: p.update(arms=["ordinary", "ordinary"]),
            lambda p: p.update(arms=["ordinary", "skill", "extra"]),
            lambda p: p.update(sources=[]),
            lambda p: p.update(task=" "),
            lambda p: p.update(unknown_extension=True),
        ]
        for change in changes:
            protocol = copy.deepcopy(PROTOCOL)
            change(protocol)
            with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                scorer.freeze_protocol(protocol)

    def test_unknown_duplicate_and_unnominated_sources_are_rejected(self):
        variants = [
            self.observation("ordinary", ["unknown"]),
            self.observation("ordinary", ["context", "context"]),
            self.observation("ordinary", [], [{"source_id": "context", "sha256": None}]),
            self.observation("ordinary", ["context"], [{"source_id": "context", "sha256": None}] * 2),
            self.observation("ordinary", ["context"], [{"source_id": "context", "sha256": "etag"}]),
        ]
        for observation in variants:
            with self.subTest(observation=observation), self.assertRaises(ValueError):
                scorer.score_trial(self.frozen, [observation, self.observation("skill")])

    def test_invalid_scalar_measurements_are_rejected(self):
        for field in scorer.SCALAR_METRICS:
            invalid = [True, False, -1, "0", math.nan, math.inf, -math.inf]
            if field in scorer.SCALAR_METRICS[:2]:
                invalid.append(0.5)
            else:
                invalid.append(10 ** 1000)
            for value in invalid:
                observations = self.valid_observations()
                observations[0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    scorer.score_trial(self.frozen, observations)

    def test_scoring_does_not_open_source_identifiers(self):
        observations = self.valid_observations()
        with mock.patch("builtins.open", side_effect=AssertionError("unexpected filesystem access")), mock.patch("io.open", side_effect=AssertionError("unexpected filesystem access")):
            result = scorer.score_trial(self.frozen, observations)
        self.assertEqual(result["arms"][0]["metrics"]["missed_important_sources"]["count"], 2)

    def test_real_cli_freezes_scores_and_refuses_overwrite(self):
        protocol = self.write("protocol.json", PROTOCOL)
        frozen = self.base / "frozen.json"
        command = [sys.executable, str(SCRIPTS / "score_trial.py")]
        result = subprocess.run(command + ["freeze", "--protocol", str(protocol), "--output", str(frozen)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(frozen.read_text()), self.frozen)
        first = self.write("ordinary.json", self.observation("ordinary"))
        second = self.write("skill.json", self.observation("skill"))
        output = self.base / "result.json"
        args = command + ["score", "--protocol", str(frozen), "--observation", str(first), "--observation", str(second), "--output", str(output)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = output.read_bytes()
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 1)
        self.assertEqual(output.read_bytes(), before)
        before = frozen.read_bytes()
        self.assertEqual(self.run_main("freeze", "--protocol", protocol, "--output", frozen), 1)
        self.assertEqual(frozen.read_bytes(), before)

    def test_cli_invalid_inputs_do_not_create_output(self):
        frozen = self.write("frozen.json", self.frozen)
        first = self.write("ordinary.json", self.observation("ordinary"))
        bad = self.write("wrong.json", self.observation("unknown"))
        output = self.base / "result.json"
        self.assertEqual(self.run_main("score", "--protocol", frozen, "--observation", first, "--observation", bad, "--output", output), 1)
        self.assertFalse(output.exists())
        for invalid_json in ('{"trial_id":"a","trial_id":"b"}', '{"elapsed_seconds":NaN}', '{"elapsed_seconds":Infinity}', '{'):
            malformed = self.base / "malformed.json"
            malformed.write_text(invalid_json)
            self.assertEqual(self.run_main("freeze", "--protocol", malformed, "--output", output), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
