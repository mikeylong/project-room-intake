#!/usr/bin/env python3
"""Inventory approved directories or exact files; content enrichment is opt-in."""

from __future__ import annotations

import argparse
import errno
import fnmatch
import hashlib
import math
import mimetypes
import os
import pwd
import stat
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from intake_store import SCHEMA_VERSION, publish_room

DEFAULT_EXCLUDES = [
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".DS_Store",
    ".cache", ".venv", "venv", "dist", "build",
]
EXIT_CODES = {"complete": 0, "partial": 3, "failed": 4}
PREVIEW_BYTES = 8192
PREVIEW_MAX_SIZE = 5 * 1024 * 1024


def absolute_path(value: str | Path) -> Path:
    # Do not resolve symlinks: that would change the approved boundary.
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", default=[], help="Approved directory root. Repeatable.")
    parser.add_argument("--file", action="append", default=[], help="Approved exact file, without listing siblings. Repeatable.")
    parser.add_argument("--room", required=True, help="Output room, separate from source inputs.")
    parser.add_argument("--query", default="", help="Optional relevance hint.")
    parser.add_argument("--include-glob", action="append", default=[])
    parser.add_argument("--exclude-glob", action="append", default=[])
    parser.add_argument("--metadata-only", action="store_true", help="Forbid content reads (the default).")
    parser.add_argument("--hash-content", action="store_true", help="Read eligible source contents to compute SHA-256.")
    parser.add_argument("--hash-max-mb", type=float, default=50.0)
    parser.add_argument("--snippets", action=argparse.BooleanOptionalAction, default=False,
                        help="Read up to 8192 bytes for short previews. --no-snippets disables only previews.")
    args = parser.parse_args(argv)
    if not args.root and not args.file:
        parser.error("at least one --root or --file is required")
    if args.metadata_only and (args.hash_content or args.snippets):
        parser.error("--metadata-only cannot be combined with --hash-content or --snippets")
    if not math.isfinite(args.hash_max_mb) or args.hash_max_mb < 0:
        parser.error("--hash-max-mb must be a finite, nonnegative number")
    if any(".." in Path(os.path.expanduser(value)).parts for value in [*args.root, *args.file, args.room]):
        parser.error("source and room paths must not contain '..'; name the intended path directly")
    # Keep every requested occurrence for provenance; candidate rows are deduplicated.
    args.root = [str(absolute_path(root)) for root in args.root]
    args.file = [str(absolute_path(path)) for path in args.file]
    args.room = str(absolute_path(args.room))
    room = Path(args.room)
    for source in map(Path, [*args.root, *args.file]):
        if room == source or room in source.parents or source in room.parents:
            parser.error("room and source inputs must not overlap; choose a separate output directory")
    return args


def utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def matches_any(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern) for pattern in patterns)


def excluded_reason(rel: str, patterns: list[str]) -> str | None:
    if matches_any(rel, patterns):
        return "user_exclude"
    if any(part.startswith(".") for part in Path(rel).parts):
        return "hidden"
    if matches_any(rel, DEFAULT_EXCLUDES):
        return "default_exclude"
    return None


def user_excluded(path: Path, patterns: list[str], roots: list[str]) -> bool:
    """User exclusions remain effective across explicit and recursive inputs.

    Check ancestor names without inspecting them: excluding a directory also
    excludes a named file below it. Root-relative patterns retain their meaning
    when a file is supplied both ways.
    """
    if not patterns:
        return False
    containing_roots = [Path(root) for root in roots if Path(root) == path or Path(root) in path.parents]
    boundary = min(containing_roots, key=lambda root: len(root.parts)) if containing_roots else None
    paths = []
    for item in (path, *path.parents):
        paths.append(item)
        if item == boundary:
            break
    if any(matches_any(str(item), patterns) for item in paths):
        return True
    for root in map(Path, roots):
        if root in path.parents:
            for item in paths:
                if item == root:
                    break
                if matches_any(item.relative_to(root).as_posix(), patterns):
                    return True
    return False


def source_id(source_type: str, path: str) -> str:
    return "src_" + hashlib.sha1(f"{source_type}:{path}".encode("utf-8")).hexdigest()[:16]


def query_terms(query: str) -> list[str]:
    words = "".join(ch.lower() if ch.isalnum() else " " for ch in query).split()
    return sorted({word for word in words if len(word) >= 3})


def relevance(record: dict, terms: list[str]) -> tuple[int, str]:
    score, reasons = 0, []
    for term in terms:
        for field, weight, label in (("name", 3, "name"), ("relative_path", 2, "path"), ("snippet", 1, "snippet")):
            if term in record[field].lower():
                score += weight
                reasons.append(f"{label}:{term}")
                break
    return score, ";".join(reasons)


def error_record(root: Path, path: Path, operation: str, exc: OSError) -> dict:
    return {"root": str(root), "path": str(path), "operation": operation,
            "error": type(exc).__name__, "message": str(exc)}


def open_root(root: Path) -> int:
    """Open directory components without following any symlink component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def fingerprint(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def enrich(name: str, parent_fd: int, info: os.stat_result, args: argparse.Namespace) -> tuple[dict, OSError | None]:
    result = {"sha256": "", "hash_status": "not_requested", "snippet": "",
              "snippet_status": "not_requested", "revision": None, "retrieval_status": "metadata_only"}
    if not args.hash_content and not args.snippets:
        return result, None
    skip = "symlink_skipped" if stat.S_ISLNK(info.st_mode) else "unsupported_type"
    if not stat.S_ISREG(info.st_mode):
        if args.hash_content:
            result["hash_status"] = skip
        if args.snippets:
            result["snippet_status"] = skip
        return result, None

    hash_requested = args.hash_content and info.st_size <= args.hash_max_mb * 1024 * 1024
    preview_requested = args.snippets and info.st_size <= PREVIEW_MAX_SIZE
    if args.hash_content:
        result["hash_status"] = "too_large" if not hash_requested else "error"
    if args.snippets:
        result["snippet_status"] = "too_large" if not preview_requested else "error"
    if not hash_requested and not preview_requested:
        return result, None

    fd = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or fingerprint(before) != fingerprint(info):
            raise OSError(errno.ESTALE, "source changed before content read")
        result["retrieval_status"] = "content_read"
        digest = hashlib.sha256() if hash_requested else None
        preview = bytearray()
        # Bound even hash reads by the observed size, plus one byte to detect growth.
        remaining = info.st_size + 1 if hash_requested else min(PREVIEW_BYTES, info.st_size)
        read_count = 0
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            read_count += len(chunk)
            remaining -= len(chunk)
            if digest is not None:
                digest.update(chunk)
            if preview_requested and len(preview) < PREVIEW_BYTES:
                preview.extend(chunk[:PREVIEW_BYTES - len(preview)])
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (fingerprint(after) != fingerprint(info) or fingerprint(current) != fingerprint(info)
                or (hash_requested and read_count != info.st_size)):
            raise OSError(errno.ESTALE, "source changed during content read")
        if digest is not None:
            value = digest.hexdigest()
            result.update(sha256=value, hash_status="hashed", revision={"kind": "sha256", "value": value})
        if preview_requested:
            if b"\x00" in preview:
                result["snippet_status"] = "binary_skipped"
            else:
                try:
                    text = preview.decode("utf-8")
                except UnicodeDecodeError:
                    text = preview.decode("latin-1")
                result["snippet"] = " ".join(text.split())[:600]
                result["snippet_status"] = "captured" if result["snippet"] else "empty"
        return result, None
    except OSError as exc:
        status = "changed" if exc.errno == errno.ESTALE else "error"
        result.update(sha256="", snippet="", revision=None, retrieval_status="content_error")
        if hash_requested:
            result["hash_status"] = status
        if preview_requested:
            result["snippet_status"] = status
        return result, exc
    finally:
        if fd is not None:
            os.close(fd)


def collect_scan(args: argparse.Namespace) -> dict:
    by_id, exclusions, coverage_errors, enrichment_errors, inputs = {}, [], [], [], []
    terms = query_terms(args.query)
    requested = [("root", path) for path in args.root] + [("file", path) for path in args.file]
    for input_kind, input_text in requested:
        source = Path(input_text)
        root = source if input_kind == "root" else source.parent
        provenance = {"kind": input_kind, "path": str(source)}
        outcome = {"root": str(source), **provenance, "status": "complete", "inspected_count": 0,
                   "error_count": 0, "exclusion_count": 0}

        def fail(path: Path, operation: str, exc: OSError) -> None:
            coverage_errors.append(error_record(source, path, operation, exc))
            outcome["error_count"] += 1

        def exclude(path: Path, reason: str, kind: str) -> None:
            exclusions.append({"root": str(source), "path": str(path), "relative_path": str(path.relative_to(root)),
                               "reason": reason, "kind": kind})
            outcome["exclusion_count"] += 1

        def observe(path: Path, parent_fd: int, info: os.stat_result) -> None:
            outcome["inspected_count"] += 1
            kind = "local_symlink" if stat.S_ISLNK(info.st_mode) else "local_file"
            identity = source_id(kind, str(path))
            if identity in by_id:
                # The first observation remains intact; an overlap adds its input
                # provenance without repeating content enrichment.
                by_id[identity]["discovered_via"].append(dict(provenance))
                return
            record = {
                "source_id": identity, "source_type": kind,
                "root": str(root), "path": str(path), "relative_path": path.relative_to(root).as_posix(), "name": path.name,
                "extension": path.suffix.lower().lstrip("."), "mime_type": mimetypes.guess_type(path.name)[0] or "",
                "size_bytes": info.st_size, "modified_at": utc_iso(info.st_mtime),
                "owner": owner_name(info.st_uid), "mode": stat.filemode(info.st_mode),
                "provider": None, "version": None,
                "metadata_token": {"size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns},
                "duplicate_hint": "", "relevance_score": 0, "relevance_reasons": "",
                "discovered_via": [dict(provenance)],
            }
            enrichment, error = enrich(path.name, parent_fd, info, args)
            record.update(enrichment)
            if error is not None:
                enrichment_errors.append(error_record(source, path, "content_read", error))
            record["relevance_score"], record["relevance_reasons"] = relevance(record, terms)
            by_id[identity] = record

        def visit(directory: Path, directory_fd: int) -> None:
            # A successful empty listing is coverage; merely opening a root is not.
            try:
                with os.scandir(directory_fd) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError as exc:
                fail(directory, "scandir", exc)
                return
            outcome["inspected_count"] += 1
            for entry in entries:
                path = directory / entry.name
                rel = path.relative_to(root).as_posix()
                reason = "user_exclude" if user_excluded(path, args.exclude_glob, args.root) else excluded_reason(rel, [])
                if reason:
                    # Intentional exclusions need no stat, much less content access.
                    exclude(path, reason, "entry")
                    continue
                try:
                    info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    fail(path, "stat", exc)
                    continue
                if stat.S_ISDIR(info.st_mode):
                    child_fd = None
                    try:
                        child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise OSError(errno.ESTALE, "directory changed before traversal")
                        visit(path, child_fd)
                    except OSError as exc:
                        fail(path, "open_directory", exc)
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
                    continue
                if args.include_glob and not matches_any(rel, args.include_glob):
                    exclude(path, "not_included", "entry")
                    continue
                observe(path, directory_fd, info)

        root_fd = None
        try:
            if user_excluded(source, args.exclude_glob, args.root):
                exclude(source, "user_exclude", "entry")
            elif input_kind == "file":
                # Exact authorization bypasses hidden/default filters, never the
                # user's filters. Filtering needs no filesystem inspection.
                if args.include_glob and not matches_any(str(source), args.include_glob):
                    exclude(source, "not_included", "entry")
                else:
                    root_fd = open_root(root)
                    info = os.stat(source.name, dir_fd=root_fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        raise IsADirectoryError(errno.EISDIR, "--file requires a file; use an authorized --root for directories", str(source))
                    observe(source, root_fd, info)
            else:
                root_fd = open_root(root)
                visit(root, root_fd)
        except OSError as exc:
            fail(source, "inspect_file" if input_kind == "file" else "open_root", exc)
        finally:
            if root_fd is not None:
                os.close(root_fd)
        if outcome["error_count"]:
            outcome["status"] = "partial" if outcome["inspected_count"] else "failed"
        inputs.append(outcome)

    records = list(by_id.values())
    records.sort(key=lambda record: record["path"])
    add_duplicate_hints(records)
    coverage = "complete"
    if coverage_errors:
        coverage = "partial" if any(item["inspected_count"] or item["status"] == "complete" for item in inputs) else "failed"
    mode = "hash+preview" if args.hash_content and args.snippets else "hash" if args.hash_content else "preview" if args.snippets else "metadata"
    return {
        "schema_version": SCHEMA_VERSION, "scan_id": uuid.uuid4().hex,
        "generated_at": utc_iso(datetime.now(timezone.utc).timestamp()),
        "roots": list(args.root), "files": list(args.file), "query": args.query,
        "filters": {"include": list(args.include_glob), "exclude": list(args.exclude_glob)},
        "content_access": {"mode": mode, "hashes": args.hash_content, "snippets": args.snippets},
        "coverage": {"status": coverage, "inputs": inputs, "errors": coverage_errors},
        "enrichment": {"status": "partial" if enrichment_errors else "complete" if args.hash_content or args.snippets else "not_requested",
                       "errors": enrichment_errors},
        "candidates": records, "exclusions": exclusions,
    }


def add_duplicate_hints(records: list[dict]) -> None:
    by_hash, by_size = defaultdict(list), defaultdict(list)
    for record in records:
        if record["sha256"]:
            by_hash[record["sha256"]].append(record)
        elif record["size_bytes"]:
            by_size[record["size_bytes"]].append(record)
    for digest, group in by_hash.items():
        if len(group) > 1:
            for record in group:
                record["duplicate_hint"] = f"same_sha256:{digest[:12]}"
    for size, group in by_size.items():
        if len(group) > 1:
            for record in group:
                record["duplicate_hint"] = f"same_size:{size}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = collect_scan(args)
    try:
        publish_room(Path(args.room), payload)
    except (OSError, ValueError) as exc:
        print(f"Room not published: {exc}", file=sys.stderr)
        return 5
    status = payload["coverage"]["status"]
    print(f"Discovery {status}: {len(payload['candidates'])} candidates, "
          f"{len(payload['coverage']['errors'])} coverage errors, "
          f"{len(payload['enrichment']['errors'])} enrichment errors. Room: {args.room}")
    return EXIT_CODES[status]


if __name__ == "__main__":
    raise SystemExit(main())
