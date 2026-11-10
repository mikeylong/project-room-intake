"""Repeatable smoke lifecycle, evidence preservation, and cleanup refusals.

Room documents in these tests are synthetic checkpoint fixtures. Passing them
exercises artifact handling, not an agent's reasoning or source-use behavior.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import smoke_test as smoke  # noqa: E402


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="intake-smoke-tests-", dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.temp_parent = self.base / "fixtures"
        self.temp_parent.mkdir()
        self.output = self.base / "evidence"

    def command(self, stage, *, output=None, skill=None):
        args = [sys.executable, str(SCRIPTS / "smoke_test.py"), stage,
                "--output", str(output or self.output)]
        if stage in ("run", "prepare"):
            args += ["--temp-parent", str(self.temp_parent)]
            if skill is not None:
                args += ["--skill-dir", str(skill)]
        return subprocess.run(args, capture_output=True, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def assert_command(self, stage, expected=0, **kwargs):
        result = self.command(stage, **kwargs)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def read(self, relative):
        return json.loads((self.output / relative).read_text(encoding="utf-8"))

    def checkpoint(self, refreshed=False, extra=""):
        """Write explicit test artifacts, without pretending to run an agent."""
        label = "after-refresh" if refreshed else "initial"
        identities = []
        for name in smoke.DOCUMENTS:
            path = self.output / "room" / name
            path.write_text(f"Synthetic {label} artifact fixture: {name}\n", encoding="utf-8")
            identities.append(f"{name}: {hashlib.sha256(path.read_bytes()).hexdigest()}")
        review = self.output / f"room-review-{label}.md"
        review.write_text("Synthetic checkpoint test; not an agent review.\n" + "\n".join(identities), encoding="utf-8")
        draft = self.output / f"draft-{label}.md"
        draft.write_text("Synthetic scripted fixture. Four volunteers attend a 30-minute orientation on November 23, 2026. "
                         "The coordinator is responsible for readiness confirmation. " + extra, encoding="utf-8")
        # Make ordering explicit on filesystems with different timestamp precision.
        stamp = max(review.stat().st_mtime_ns, draft.stat().st_mtime_ns)
        os.utime(review, ns=(stamp, stamp))
        os.utime(draft, ns=(stamp + 1_000_000, stamp + 1_000_000))

    def test_real_cli_run_preserves_evidence_boundaries_and_target_then_cleans(self):
        before = smoke.skill_identity(SCRIPTS.parent)
        self.assert_command("run")
        receipt = self.read("result.json")
        self.assertEqual(receipt["result"], "passed")
        self.assertEqual(receipt["mode"], "cli")
        self.assertTrue(receipt["fixtures_deleted"])
        self.assertFalse(os.path.lexists(receipt["fixture_path"]))
        self.assertEqual(list(self.temp_parent.iterdir()), [])
        self.assertEqual(self.read("installation-before.json")["files"], before)
        self.assertEqual(self.read("installation-after.json")["files"], before)
        self.assertEqual(smoke.skill_identity(SCRIPTS.parent), before)
        commands = [json.loads(p.read_text()) for p in (self.output / "commands").glob("*.json")]
        self.assertEqual({r["exit_code"] for r in commands}, {0, 1, 2, 3, 4, 5})
        for path in (self.output / "access").glob("*.json"):
            access = json.loads(path.read_text())
            self.assertFalse(access["guard_failed"], path.name)
            self.assertEqual(access["forbidden_access_attempts"], [], path.name)
            policy = self.read("guard-configs/" + path.name)
            if not policy["permitted_content_names"]:
                self.assertEqual(access["source_content_open_attempts"], [], path.name)
        original_history = (self.output / "phases/03-reviewed/review_decisions.json").read_bytes()
        for phase in ("04-metadata-refresh", "05-hash-refresh", "07-partial-unavailable-root", "08-recovered"):
            self.assertEqual((self.output / "phases" / phase / "review_decisions.json").read_bytes(), original_history)
        rows = {Path(c["path"]).name: c for c in self.read("room/candidate_sources.json")["candidates"]}
        self.assertEqual(rows["plan.md"]["review"]["status"], "needs_review")
        self.assertEqual(rows["new-notes.md"]["review"]["status"], "unreviewed")
        self.assertEqual(rows["retire.md"]["observation_status"], "missing")
        for phase in ("initial", "changed"):
            archive = self.output / "fixture-snapshots" / phase
            self.assertEqual(smoke.inventory(archive), self.read(f"fixture-snapshots/{phase}-manifest.json"))
            self.assertTrue((archive / "sources/outside-link.md").is_symlink())
        self.assertFalse((self.output / "draft-initial.md").exists())

    def test_existing_output_is_refused_without_changing_it_or_creating_fixtures(self):
        self.output.mkdir()
        sentinel = self.output / "existing.txt"
        sentinel.write_bytes(b"preserve existing evidence\n")
        before = smoke.inventory(self.output)
        self.assert_command("run", expected=1)
        self.assertEqual(smoke.inventory(self.output), before)
        self.assertEqual(list(self.temp_parent.iterdir()), [])

    def test_initial_state_write_failure_rolls_back_empty_fixture_directory(self):
        write_json = smoke.write_json

        def fail_initial_state(path, value):
            if path == self.output / "state.json":
                raise OSError("synthetic initial state write failure")
            return write_json(path, value)

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(smoke, "write_json", side_effect=fail_initial_state):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = smoke.main(["run", "--output", str(self.output),
                                   "--temp-parent", str(self.temp_parent)])
        self.assertEqual(code, 1)
        self.assertEqual(list(self.temp_parent.iterdir()), [])
        self.assertFalse((self.output / "result.json").exists())
        self.assertIn("initialization failed", stderr.getvalue())
        self.assertIn("fixture removed", stderr.getvalue())
        self.assertIn(str(self.temp_parent / "project-room-intake-smoke-"), stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_missing_checkpoint_refuses_refresh_before_any_source_mutation(self):
        self.assert_command("prepare")
        fixture = Path(self.read("state.json")["fixtures"])
        before = smoke.inventory(fixture)
        self.assert_command("refresh", expected=1)
        self.assertEqual(smoke.inventory(fixture), before)
        self.assertEqual(self.read("result.json")["result"], "failed")
        self.assertFalse((self.output / "phases/04-metadata-refresh").exists())
        self.assert_command("cleanup")
        self.assertFalse(fixture.exists())
        self.assertEqual(self.read("result.json")["result"], "abandoned")

    def test_tampered_ownership_marker_blocks_cleanup_and_preserves_sources(self):
        self.assert_command("prepare")
        fixture = Path(self.read("state.json")["fixtures"])
        marker = fixture / ".smoke-owned"
        original_marker = marker.read_bytes()
        marker.write_text('{"run_id":"another-run","output":"unrelated"}', encoding="utf-8")
        before = smoke.inventory(fixture)
        self.assert_command("cleanup", expected=1)
        self.assertEqual(smoke.inventory(fixture), before)
        self.assertFalse(self.read("result.json")["fixtures_deleted"])
        marker.write_bytes(original_marker)
        self.assert_command("cleanup")
        self.assertFalse(fixture.exists())

    def test_failed_evidence_export_retains_fixture_and_reports_failure(self):
        self.assert_command("prepare")
        fixture = Path(self.read("state.json")["fixtures"])
        before = smoke.inventory(fixture)
        with mock.patch.object(smoke.Smoke, "archive", side_effect=OSError("synthetic archive failure")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = smoke.main(["cleanup", "--output", str(self.output)])
        self.assertEqual(code, 1)
        self.assertEqual(smoke.inventory(fixture), before)
        receipt = self.read("result.json")
        self.assertEqual(receipt["result"], "failed")
        self.assertFalse(receipt["fixtures_deleted"])
        self.assert_command("cleanup")
        self.assertFalse(fixture.exists())

    def test_scanner_failure_is_not_success_and_one_command_run_still_cleans(self):
        broken = self.base / "broken-skill"
        broken.mkdir()
        shutil.copy2(SCRIPTS.parent / "SKILL.md", broken / "SKILL.md")
        shutil.copytree(SCRIPTS, broken / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (broken / "scripts/scan_sources.py").write_text("raise SystemExit(17)\n", encoding="utf-8")
        self.assert_command("run", expected=1, skill=broken)
        receipt = self.read("result.json")
        self.assertEqual(receipt["result"], "failed")
        self.assertTrue(receipt["fixtures_deleted"])
        self.assertFalse(os.path.lexists(receipt["fixture_path"]))
        self.assertEqual(self.read("commands/01-metadata.json")["exit_code"], 17)
        self.assertEqual(self.read("access/01-metadata.json")["scanner_exit"], 17)
        self.assertTrue(any((self.output / "fixture-snapshots").glob("cleanup-*-manifest.json")))
        self.assertEqual(list(self.temp_parent.iterdir()), [])

    def test_staged_synthetic_artifact_flow_preserves_history_and_success_receipt(self):
        self.assert_command("prepare")
        fixture = Path(self.read("state.json")["fixtures"])
        history = (self.output / "room/review_decisions.json").read_bytes()
        self.checkpoint()
        initial_draft = (self.output / "draft-initial.md").read_bytes()
        initial_review = (self.output / "room-review-initial.md").read_bytes()
        initial_docs = {name: (self.output / "room" / name).read_bytes() for name in smoke.DOCUMENTS}
        self.assert_command("refresh")
        self.assertTrue(fixture.exists())
        for name, content in initial_docs.items():
            self.assertEqual((self.output / "initial-room-documents" / name).read_bytes(), content)
        self.checkpoint(refreshed=True)
        self.assert_command("finish")
        self.assertFalse(fixture.exists())
        self.assertEqual((self.output / "draft-initial.md").read_bytes(), initial_draft)
        self.assertEqual((self.output / "room-review-initial.md").read_bytes(), initial_review)
        self.assertEqual((self.output / "room/review_decisions.json").read_bytes(), history)
        receipt = self.read("result.json")
        self.assertEqual(receipt["result"], "passed")
        self.assertEqual(receipt["mode"], "staged")
        receipt_bytes = (self.output / "result.json").read_bytes()
        self.assert_command("cleanup")
        self.assertEqual((self.output / "result.json").read_bytes(), receipt_bytes)

    def test_refreshed_draft_using_unapproved_fixture_fact_cannot_pass(self):
        self.assert_command("prepare")
        self.checkpoint()
        self.assert_command("refresh")
        fixture = Path(self.read("state.json")["fixtures"])
        self.checkpoint(refreshed=True, extra="Move kickoff to November 25.")
        self.assert_command("finish", expected=1)
        receipt = self.read("result.json")
        self.assertEqual(receipt["result"], "failed")
        self.assertFalse(receipt["fixtures_deleted"])
        self.assertTrue(fixture.exists())
        self.assert_command("cleanup")
        self.assertFalse(fixture.exists())

    def test_intervening_source_edit_blocks_refresh_before_planned_deletion(self):
        self.assert_command("prepare")
        self.checkpoint()
        fixture = Path(self.read("state.json")["fixtures"])
        retired = fixture / "sources/retire.md"
        edited = b"Unexpected intervening edit must survive a refused refresh.\n"
        retired.write_bytes(edited)
        before = smoke.inventory(fixture)
        self.assert_command("refresh", expected=1)
        self.assertEqual(self.read("state.json")["stage"], "failed")
        self.assertEqual(self.read("result.json")["result"], "failed")
        self.assertEqual(retired.read_bytes(), edited)
        self.assertEqual(smoke.inventory(fixture), before)
        self.assertFalse((self.output / "phases/04-metadata-refresh").exists())
        self.assert_command("cleanup")
        self.assertFalse(fixture.exists())
        exports = list((self.output / "fixture-snapshots").glob("cleanup-*/sources/retire.md"))
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0].read_bytes(), edited)

    def test_intervening_approved_source_edit_blocks_finish_and_preserves_bytes(self):
        self.assert_command("prepare")
        self.checkpoint()
        self.assert_command("refresh")
        self.checkpoint(refreshed=True)
        fixture = Path(self.read("state.json")["fixtures"])
        brief = fixture / "sources/brief.md"
        edited = b"Previously approved brief changed after the refresh scan.\n"
        brief.write_bytes(edited)
        before = smoke.inventory(fixture)
        self.assert_command("finish", expected=1)
        self.assertEqual(self.read("state.json")["stage"], "failed")
        self.assertEqual(self.read("result.json")["result"], "failed")
        self.assertFalse(self.read("result.json")["fixtures_deleted"])
        self.assertEqual(brief.read_bytes(), edited)
        self.assertEqual(smoke.inventory(fixture), before)
        self.assert_command("cleanup")
        self.assertFalse(fixture.exists())
        exports = list((self.output / "fixture-snapshots").glob("cleanup-*/sources/brief.md"))
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0].read_bytes(), edited)


if __name__ == "__main__":
    unittest.main()
