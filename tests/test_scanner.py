"""Behavioral checks for bounded discovery and its content-access contract."""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import scan_sources as scanner  # noqa: E402


class ScannerTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir()).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="intake-scanner-tests-", dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "sources"
        self.root.mkdir()
        self.room = self.base / "room"

    def arguments(self, *options, roots=None, room=None):
        argv = ["--room", str(room or self.room)]
        for root in roots if roots is not None else [self.root]:
            argv += ["--root", str(root)]
        return argv + list(options)

    def collect(self, *options, roots=None):
        return scanner.collect_scan(scanner.parse_args(self.arguments(*options, roots=roots)))

    def run_main(self, *options, roots=None, room=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return scanner.main(self.arguments(*options, roots=roots, room=room))

    def records(self, payload):
        return {record["name"]: record for record in payload["candidates"]}

    @contextlib.contextmanager
    def deny_source_opens(self, names):
        """Trap content opens through all Python APIs the scanner could use.

        Directory descriptors are allowed: they are necessary to enumerate and
        stat without resolving symlinks. Source files (including empty files)
        must never acquire content descriptors in metadata-only mode.
        """
        names = set(names)
        real_builtin_open = builtins.open
        real_io_open = io.open
        real_os_open = os.open

        def is_source(file):
            return not isinstance(file, int) and Path(os.fspath(file)).name in names

        def checked_builtin(file, *args, **kwargs):
            if is_source(file):
                raise AssertionError(f"unexpected source-content open: {file}")
            return real_builtin_open(file, *args, **kwargs)

        def checked_io(file, *args, **kwargs):
            if is_source(file):
                raise AssertionError(f"unexpected source-content open: {file}")
            return real_io_open(file, *args, **kwargs)

        def checked_os(file, flags, *args, **kwargs):
            if is_source(file) and not (flags & os.O_DIRECTORY):
                raise AssertionError(f"unexpected source descriptor: {file}")
            return real_os_open(file, flags, *args, **kwargs)

        with mock.patch("builtins.open", checked_builtin), mock.patch("io.open", checked_io), mock.patch("os.open", checked_os):
            yield

    def test_default_metadata_mode_never_opens_text_binary_empty_or_symlink_contents(self):
        (self.root / "text.txt").write_text("do not read this source")
        (self.root / "empty.txt").touch()
        (self.root / "binary.dat").write_bytes(b"\x00\xfffixture")
        external = self.base / "outside.txt"
        external.write_text("outside the boundary")
        (self.root / "shortcut.txt").symlink_to(external)
        external_dir = self.base / "outside-dir"
        external_dir.mkdir()
        (external_dir / "secret.txt").write_text("not in approved scope")
        (self.root / "shortcut-dir").symlink_to(external_dir, target_is_directory=True)
        with self.deny_source_opens({"text.txt", "empty.txt", "binary.dat", "outside.txt", "shortcut.txt", "secret.txt"}):
            payload = self.collect()
        rows = self.records(payload)
        self.assertTrue({"text.txt", "empty.txt", "binary.dat", "shortcut.txt"}.issubset(rows))
        self.assertNotIn("secret.txt", rows)
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertEqual(payload["content_access"]["mode"], "metadata")
        self.assertFalse(payload["content_access"]["hashes"])
        self.assertFalse(payload["content_access"]["snippets"])
        self.assertEqual(payload["enrichment"]["status"], "not_requested")
        for row in rows.values():
            self.assertFalse(row["sha256"])
            self.assertFalse(row["snippet"])
            self.assertIsNone(row["revision"])
            self.assertIn(row["hash_status"], {"not_requested", "symlink_skipped", "disabled", "unsupported"})
            self.assertIn(row["snippet_status"], {"not_requested", "symlink_skipped", "disabled", "unsupported"})
            self.assertIsInstance(row["size_bytes"], int)
            self.assertIsInstance(row["relevance_score"], int)

    def test_explicit_metadata_mode_does_not_hash_an_empty_file(self):
        (self.root / "empty.txt").touch()
        with self.deny_source_opens({"empty.txt"}):
            payload = self.collect("--metadata-only")
        row = self.records(payload)["empty.txt"]
        self.assertFalse(row["sha256"])
        self.assertIsNone(row["revision"])

    def test_symlink_root_is_not_followed(self):
        (self.root / "private.txt").write_text("private")
        link = self.base / "linked-root"
        link.symlink_to(self.root, target_is_directory=True)
        with self.deny_source_opens({"private.txt"}):
            payload = self.collect("--hash-content", roots=[link])
        self.assertEqual(payload["coverage"]["status"], "failed")
        self.assertTrue(payload["coverage"]["errors"])
        self.assertEqual(payload["candidates"], [])

    def test_parent_components_cannot_hide_a_symlink_boundary(self):
        link = self.base / "linked-root"
        link.symlink_to(self.root, target_is_directory=True)
        ambiguous_root = link / ".." / "sources"
        with self.deny_source_opens({"private.txt"}), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.collect("--hash-content", roots=[ambiguous_root])
        self.assertEqual(raised.exception.code, 2)

    def test_symlink_ancestor_is_not_followed(self):
        child = self.root / "nested"
        child.mkdir()
        (child / "private.txt").write_text("private")
        link = self.base / "linked-parent"
        link.symlink_to(self.root, target_is_directory=True)
        with self.deny_source_opens({"private.txt"}):
            payload = self.collect(roots=[link / "nested"])
        self.assertEqual(payload["coverage"]["status"], "failed")
        self.assertEqual(payload["candidates"], [])

    def test_explicit_hashing_with_no_snippets_has_verified_revision(self):
        content = b"authorized content hashing\n"
        (self.root / "sample.txt").write_bytes(content)
        payload = self.collect("--hash-content", "--no-snippets")
        row = self.records(payload)["sample.txt"]
        digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(row["sha256"], digest)
        self.assertEqual(row["revision"], {"kind": "sha256", "value": digest})
        self.assertFalse(row["snippet"])
        self.assertTrue(payload["content_access"]["hashes"])
        self.assertFalse(payload["content_access"]["snippets"])
        self.assertEqual(payload["enrichment"]["status"], "complete")

    def test_preview_reads_are_bounded_and_do_not_compute_hashes(self):
        sample = self.root / "large.txt"
        sample.write_bytes(b"prefix word " * 100_000)
        identity = (sample.stat().st_dev, sample.stat().st_ino)
        actual_read = os.read
        read_bytes = []

        def count_read(fd, count):
            result = actual_read(fd, count)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == identity:
                read_bytes.append(len(result))
            return result

        with mock.patch("os.read", count_read):
            payload = self.collect("--snippets")
        row = self.records(payload)["large.txt"]
        self.assertTrue(read_bytes, "test must observe actual source reads")
        self.assertLessEqual(sum(read_bytes), 8192)
        self.assertTrue(row["snippet"].startswith("prefix word"))
        self.assertLessEqual(len(row["snippet"]), 600)
        self.assertFalse(row["sha256"])
        self.assertIsNone(row["revision"])

    def test_binary_preview_is_not_text(self):
        (self.root / "binary.dat").write_bytes(b"\x00\xffbinary")
        payload = self.collect("--snippets")
        row = self.records(payload)["binary.dat"]
        self.assertFalse(row["snippet"])
        self.assertEqual(row["snippet_status"], "binary_skipped")

    def test_hash_size_limit_does_not_open_over_limit_content(self):
        (self.root / "over-limit.txt").write_text("larger than zero")
        with self.deny_source_opens({"over-limit.txt"}):
            payload = self.collect("--hash-content", "--hash-max-mb", "0")
        row = self.records(payload)["over-limit.txt"]
        self.assertFalse(row["sha256"])
        self.assertEqual(row["hash_status"], "too_large")
        self.assertEqual(payload["coverage"]["status"], "complete")

    def test_enrichment_does_not_follow_source_symlink(self):
        target = self.base / "outside.txt"
        target.write_text("not approved")
        (self.root / "shortcut.txt").symlink_to(target)
        with self.deny_source_opens({"outside.txt", "shortcut.txt"}):
            payload = self.collect("--hash-content", "--snippets")
        row = self.records(payload)["shortcut.txt"]
        self.assertEqual(row["source_type"], "local_symlink")
        self.assertFalse(row["sha256"])
        self.assertFalse(row["snippet"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires a POSIX FIFO")
    def test_enrichment_never_opens_fifo(self):
        fifo = self.root / "source.fifo"
        os.mkfifo(fifo)
        with self.deny_source_opens({"source.fifo"}):
            payload = self.collect("--hash-content", "--snippets")
        self.assertEqual(payload["coverage"]["status"], "complete")
        row = self.records(payload).get("source.fifo")
        if row is not None:
            self.assertFalse(row["sha256"])
            self.assertFalse(row["snippet"])
        else:
            self.assertTrue(any(x["path"].endswith("source.fifo") for x in payload["exclusions"]))

    def test_exclusions_are_applied_before_content_enrichment(self):
        (self.root / "keep.txt").write_text("kept")
        (self.root / "skip.txt").write_text("excluded")
        hidden = self.root / ".hidden"
        hidden.mkdir()
        (hidden / "secret.txt").write_text("hidden")
        with self.deny_source_opens({"skip.txt", "secret.txt"}):
            payload = self.collect("--hash-content", "--snippets", "--exclude-glob", "skip.txt", "--include-glob", "*.txt")
        self.assertEqual(set(self.records(payload)), {"keep.txt"})
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertGreaterEqual(len(payload["exclusions"]), 2)

    def test_conflicting_content_modes_are_usage_errors(self):
        for option in ("--hash-content", "--snippets"):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    scanner.parse_args(self.arguments("--metadata-only", option))
                self.assertEqual(exc.exception.code, 2)

    def test_invalid_hash_limits_are_usage_errors(self):
        for value in ("-1", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    scanner.parse_args(self.arguments("--hash-max-mb", value))
                self.assertEqual(exc.exception.code, 2)

    def test_empty_valid_root_is_complete(self):
        payload = self.collect()
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertEqual(payload["coverage"]["errors"], [])
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(self.run_main(), 0)

    def test_all_intentionally_excluded_inputs_are_complete(self):
        (self.root / "skip.txt").write_text("excluded")
        payload = self.collect("--exclude-glob", "*")
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertEqual(payload["candidates"], [])
        self.assertTrue(payload["exclusions"])

    def test_missing_root_is_failed_and_cli_returns_four(self):
        roots = [self.base / "missing"]
        payload = self.collect(roots=roots)
        self.assertEqual(payload["coverage"]["status"], "failed")
        self.assertTrue(payload["coverage"]["errors"])
        self.assertEqual(self.run_main(roots=roots), 4)
        written = json.loads((self.room / "candidate_sources.json").read_text())
        self.assertEqual(written["coverage"]["status"], "failed")

    def test_mixed_valid_missing_roots_are_partial_and_return_three(self):
        (self.root / "visible.txt").write_text("available")
        roots = [self.root, self.base / "missing"]
        payload = self.collect(roots=roots)
        self.assertEqual(payload["coverage"]["status"], "partial")
        self.assertIn("visible.txt", self.records(payload))
        self.assertEqual(self.run_main(roots=roots), 3)
        written = json.loads((self.room / "candidate_sources.json").read_text())
        self.assertEqual(written["coverage"]["status"], "partial")

    def test_unreadable_root_reports_error_and_failed_coverage(self):
        inode = self.root.stat().st_ino
        real_scandir = os.scandir

        def deny_root(path):
            info = os.fstat(path) if isinstance(path, int) else os.stat(path)
            if info.st_ino == inode:
                raise PermissionError("synthetic root enumeration denial")
            return real_scandir(path)

        with mock.patch("os.scandir", deny_root):
            payload = self.collect()
        self.assertEqual(payload["coverage"]["status"], "failed")
        self.assertTrue(payload["coverage"]["errors"])

    def test_single_partly_readable_root_is_partial(self):
        (self.root / "visible.txt").write_text("available")
        denied = self.root / "denied"
        denied.mkdir()
        (denied / "inaccessible.txt").write_text("unavailable")
        inode = denied.stat().st_ino
        real_scandir = os.scandir

        def deny_child(path):
            info = os.fstat(path) if isinstance(path, int) else os.stat(path)
            if info.st_ino == inode:
                raise PermissionError("synthetic child enumeration denial")
            return real_scandir(path)

        with mock.patch("os.scandir", deny_child):
            payload = self.collect()
        self.assertEqual(payload["coverage"]["status"], "partial")
        self.assertIn("visible.txt", self.records(payload))
        self.assertNotIn("inaccessible.txt", self.records(payload))
        self.assertTrue(any("denied" in x["path"] for x in payload["coverage"]["errors"]))

    def test_stat_failure_is_recorded_as_partial_coverage(self):
        (self.root / "available.txt").write_text("available")
        (self.root / "stat-denied.txt").write_text("unavailable")
        real_stat = os.stat

        def deny_stat(path, *args, **kwargs):
            if not isinstance(path, int) and Path(os.fspath(path)).name == "stat-denied.txt":
                raise PermissionError("synthetic stat denial")
            return real_stat(path, *args, **kwargs)

        with mock.patch("os.stat", deny_stat):
            payload = self.collect()
        self.assertEqual(payload["coverage"]["status"], "partial")
        self.assertIn("available.txt", self.records(payload))
        self.assertTrue(any("stat-denied.txt" in x["path"] for x in payload["coverage"]["errors"]))

    def test_optional_hash_failure_does_not_make_discovery_incomplete(self):
        (self.root / "read-denied.txt").write_text("available metadata")
        real_open = os.open

        def deny_content(path, flags, *args, **kwargs):
            if Path(os.fspath(path)).name == "read-denied.txt" and not flags & os.O_DIRECTORY:
                raise PermissionError("synthetic content-open denial")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch("os.open", deny_content):
            payload = self.collect("--hash-content")
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertEqual(payload["coverage"]["errors"], [])
        self.assertEqual(payload["enrichment"]["status"], "partial")
        self.assertTrue(payload["enrichment"]["errors"])
        row = self.records(payload)["read-denied.txt"]
        self.assertFalse(row["sha256"])
        self.assertIsNone(row["revision"])

    def test_replaced_file_cannot_redirect_enrichment_to_symlink_target(self):
        sample = self.root / "sample.txt"
        sample.write_text("original approved bytes")
        outside = self.base / "outside.txt"
        outside.write_text("outside bytes")
        real_open = os.open
        swapped = []

        def replace_before_open(path, flags, *args, **kwargs):
            if Path(os.fspath(path)).name == "sample.txt" and not flags & os.O_DIRECTORY and not swapped:
                self.assertTrue(flags & os.O_NOFOLLOW)
                sample.unlink()
                sample.symlink_to(outside)
                swapped.append(True)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch("os.open", replace_before_open):
            payload = self.collect("--hash-content", "--snippets")
        self.assertTrue(swapped)
        row = self.records(payload)["sample.txt"]
        self.assertFalse(row["sha256"])
        self.assertFalse(row["snippet"])
        self.assertIsNone(row["revision"])
        self.assertEqual(payload["enrichment"]["status"], "partial")

    def test_file_changed_during_read_does_not_get_trusted_revision(self):
        sample = self.root / "changing.txt"
        sample.write_bytes(b"original bytes")
        identity = (sample.stat().st_dev, sample.stat().st_ino)
        real_read = os.read
        changed = []

        def change_during_read(fd, count):
            data = real_read(fd, count)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == identity and not changed:
                with sample.open("ab") as handle:
                    handle.write(b" changed while scanning")
                changed.append(True)
            return data

        with mock.patch("os.read", change_during_read):
            payload = self.collect("--hash-content")
        self.assertTrue(changed)
        row = self.records(payload)["changing.txt"]
        self.assertFalse(row["sha256"])
        self.assertIsNone(row["revision"])
        self.assertEqual(payload["enrichment"]["status"], "partial")

    def test_room_write_failure_returns_five(self):
        (self.root / "sample.txt").write_text("available")
        blocked = self.base / "not-a-directory"
        blocked.write_text("preserve me")
        self.assertEqual(self.run_main(room=blocked), 5)
        self.assertEqual(blocked.read_text(), "preserve me")

    def test_real_cli_publishes_consistent_metadata_artifacts(self):
        (self.root / "sample.txt").write_text("available")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "scan_sources.py"), *self.arguments()],
            text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads((self.room / "candidate_sources.json").read_text())
        self.assertEqual(payload["schema_version"], "project-room-intake.candidates.v2")
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertEqual(payload["content_access"]["mode"], "metadata")
        self.assertTrue((self.room / "candidate_sources.csv").is_file())
        self.assertIn("complete", (self.room / "scan_summary.md").read_text().lower())


if __name__ == "__main__":
    unittest.main()
