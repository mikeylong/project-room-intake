"""Regression checks for the smoke runner's Python API instrumentation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "smoke_guard.py"


class SmokeGuardTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir()).resolve()
        self.temporary = tempfile.TemporaryDirectory(prefix="intake-smoke-guard-", dir=temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.installed = self.base / "skill"
        (self.installed / "scripts").mkdir(parents=True)
        self.exact = self.base / "exact"
        self.exact.mkdir()
        self.source = self.exact / "source.md"
        self.source.write_text("synthetic source\n")
        self.sibling = self.exact / "sibling.md"
        self.sibling.write_text("unapproved sibling\n")
        self.link = self.exact / "link.md"
        self.link.symlink_to(self.source)
        self.log = self.base / "new" / "log.json"

    def run_guard(self, code=None, permitted=(), scanner_args=(), installed=None):
        if code is not None:
            (self.installed / "scripts" / "scan_sources.py").write_text(code)
        config = {
            "installed": str(installed or self.installed),
            "source_names": ["source.md", "link.md"],
            "permitted_content_names": list(permitted),
            "forbidden_stat_names": ["sibling.md"],
            "exact_parent": str(self.exact),
        }
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(config))
        process = subprocess.run(
            [sys.executable, str(GUARD), "--config", str(config_path), "--log", str(self.log), "--", *scanner_args],
            text=True, capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return process, json.loads(self.log.read_text())

    def test_metadata_open_is_blocked_even_when_scanner_swallows_permission_error(self):
        for expression in [f"open({str(self.source)!r})", f"io.open({str(self.source)!r})",
                           f"os.open({str(self.source)!r}, os.O_RDONLY)"]:
            with self.subTest(expression=expression):
                process, record = self.run_guard(
                    f"import io, os\ntry:\n    {expression}\nexcept PermissionError:\n    pass\n"
                )
                self.assertEqual(process.returncode, 97, process.stderr)
                self.assertEqual(record["scanner_exit"], 0)
                self.assertTrue(record["guard_failed"])
                self.assertEqual(len(record["source_content_open_attempts"]), 1)
                self.assertFalse(record["source_content_open_attempts"][0]["permitted"])

    def test_forbidden_metadata_is_blocked_for_stat_and_lstat(self):
        for api in ["stat", "lstat"]:
            with self.subTest(api=api):
                process, record = self.run_guard(
                    f"import os\ntry:\n    os.{api}({str(self.sibling)!r})\nexcept PermissionError:\n    pass\n"
                )
                self.assertEqual(process.returncode, 97)
                self.assertEqual(record["forbidden_access_attempts"][0]["api"], f"os.{api}")

    def test_exact_parent_enumeration_is_blocked_by_path_and_descriptor(self):
        for api in ["listdir", "scandir"]:
            for descriptor in [False, True]:
                with self.subTest(api=api, descriptor=descriptor):
                    if descriptor:
                        setup = f"target = os.open({str(self.exact)!r}, os.O_RDONLY | os.O_DIRECTORY)"
                        teardown = "os.close(target)"
                    else:
                        setup = f"target = {str(self.exact)!r}"
                        teardown = "pass"
                    process, record = self.run_guard(
                        f"import os\n{setup}\ntry:\n    os.{api}(target)\nexcept PermissionError:\n    pass\n{teardown}\n"
                    )
                    self.assertEqual(process.returncode, 97)
                    self.assertEqual(record["forbidden_access_attempts"][0]["api"], f"os.{api}")

    def test_source_symlinks_cannot_be_followed_even_with_hash_permission(self):
        for expression in [f"os.stat({str(self.link)!r})", f"open({str(self.link)!r})"]:
            with self.subTest(expression=expression):
                process, record = self.run_guard(
                    f"import os\ntry:\n    {expression}\nexcept PermissionError:\n    pass\n",
                    permitted=["link.md"],
                )
                self.assertEqual(process.returncode, 97)
                self.assertTrue(record["forbidden_access_attempts"])

    def test_symlink_metadata_and_authorized_regular_content_are_allowed(self):
        process, record = self.run_guard(
            f"import os\nos.stat({str(self.link)!r}, follow_symlinks=False)\n"
            f"with open({str(self.source)!r}) as stream:\n    assert stream.read() == 'synthetic source\\n'\n",
            permitted=["source.md"],
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(record["guard_failed"])
        self.assertTrue(record["source_content_open_attempts"][0]["permitted"])

    def test_scanner_exit_and_exception_are_preserved_in_new_log_directory(self):
        for code, expected in [("raise SystemExit(4)\n", 4), ("raise RuntimeError('scanner failed')\n", 1)]:
            with self.subTest(code=code):
                process, record = self.run_guard(code)
                self.assertEqual(process.returncode, expected)
                self.assertEqual(record["scanner_exit"], expected)
                self.assertEqual(record["runner_exit"], expected)
                self.assertFalse(record["guard_failed"])
                if expected == 1:
                    self.assertEqual(record["scanner_error"]["type"], "RuntimeError")
                self.assertFalse(list(self.installed.rglob("__pycache__")))

    def test_real_scanner_completes_metadata_and_hash_modes(self):
        for mode in [["--metadata-only"], ["--hash-content", "--no-snippets"]]:
            with self.subTest(mode=mode):
                process, record = self.run_guard(
                    installed=REPO, permitted=["source.md"] if "--hash-content" in mode else [],
                    scanner_args=["--room", str(self.base / "room"), "--file", str(self.source), *mode],
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertFalse(record["forbidden_access_attempts"])
                self.assertEqual(len(record["source_content_open_attempts"]), int("--hash-content" in mode))


if __name__ == "__main__":
    unittest.main()
