#!/usr/bin/env python3
"""Inventory approved directory roots; content enrichment is explicitly opt-in."""

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
    parser.add_argument("--root", action="append", required=True, help="Approved directory root. Repeatable.")
    parser.add_argument("--room", required=True, help="Output room, outside all source roots.")
    parser.add_argument("--query", default="", help="Optional relevance hint.")
    parser.add_argument("--include-glob", action="append", default=[])
    parser.add_argument("--exclude-glob", action="append", default=[])
    parser.add_argument("--metadata-only", action="store_true", help="Forbid content reads (the default).")
    parser.add_argument("--hash-content", action="store_true", help="Read eligible source contents to compute SHA-256.")
    parser.add_argument("--hash-max-mb", type=float, default=50.0)
    parser.add_argument("--snippets", action=argparse.BooleanOptionalAction, default=False,
                        help="Read up to 8192 bytes for short previews. --no-snippets disables only previews.")
    args = parser.parse_args(argv)
    if args.metadata_only and (args.hash_content or args.snippets):
        parser.error("--metadata-only cannot be combined with --hash-content or --snippets")
    if not math.isfinite(args.hash_max_mb) or args.hash_max_mb < 0:
        parser.error("--hash-max-mb must be a finite, nonnegative number")
    if any(".." in Path(os.path.expanduser(value)).parts for value in [*args.root, args.room]):
        parser.error("root and room paths must not contain '..'; name the intended directory directly")
    args.root = list(dict.fromkeys(str(absolute_path(root)) for root in args.root))
    args.room = str(absolute_path(args.room))
    room = Path(args.room)
    for root in map(Path, args.root):
        if room == root or room in root.parents or root in room.parents:
            parser.error("room and source roots must not overlap; choose a separate output directory")
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
    if any(part.startswith(".") for part in Path(rel).parts):
        return "hidden"
    if matches_any(rel, DEFAULT_EXCLUDES):
        return "default_exclude"
    if matches_any(rel, patterns):
        return "user_exclude"
    return None


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
    records, exclusions, coverage_errors, enrichment_errors, inputs = [], [], [], [], []
    terms = query_terms(args.query)
    for root_text in args.root:
        root = Path(root_text)
        outcome = {"root": str(root), "status": "complete", "inspected_count": 0,
                   "error_count": 0, "exclusion_count": 0}

        def fail(path: Path, operation: str, exc: OSError) -> None:
            coverage_errors.append(error_record(root, path, operation, exc))
            outcome["error_count"] += 1

        def exclude(path: Path, reason: str, kind: str) -> None:
            exclusions.append({"root": str(root), "path": str(path), "relative_path": str(path.relative_to(root)),
                               "reason": reason, "kind": kind})
            outcome["exclusion_count"] += 1

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
                reason = excluded_reason(rel, args.exclude_glob)
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
                outcome["inspected_count"] += 1
                kind = "local_symlink" if stat.S_ISLNK(info.st_mode) else "local_file"
                record = {
                    "source_id": source_id(kind, str(path)), "source_type": kind,
                    "root": str(root), "path": str(path), "relative_path": rel, "name": path.name,
                    "extension": path.suffix.lower().lstrip("."), "mime_type": mimetypes.guess_type(path.name)[0] or "",
                    "size_bytes": info.st_size, "modified_at": utc_iso(info.st_mtime),
                    "owner": owner_name(info.st_uid), "mode": stat.filemode(info.st_mode),
                    "provider": None, "version": None,
                    "metadata_token": {"size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns},
                    "duplicate_hint": "", "relevance_score": 0, "relevance_reasons": "",
                }
                enrichment, error = enrich(entry.name, directory_fd, info, args)
                record.update(enrichment)
                if error is not None:
                    enrichment_errors.append(error_record(root, path, "content_read", error))
                record["relevance_score"], record["relevance_reasons"] = relevance(record, terms)
                records.append(record)

        root_fd = None
        try:
            root_fd = open_root(root)
            visit(root, root_fd)
        except OSError as exc:
            fail(root, "open_root", exc)
        finally:
            if root_fd is not None:
                os.close(root_fd)
        if outcome["error_count"]:
            outcome["status"] = "partial" if outcome["inspected_count"] else "failed"
        inputs.append(outcome)

    records = list({record["source_id"]: record for record in records}.values())
    records.sort(key=lambda record: record["path"])
    add_duplicate_hints(records)
    coverage = "complete"
    if coverage_errors:
        coverage = "partial" if any(item["inspected_count"] for item in inputs) else "failed"
    mode = "hash+preview" if args.hash_content and args.snippets else "hash" if args.hash_content else "preview" if args.snippets else "metadata"
    return {
        "schema_version": SCHEMA_VERSION, "scan_id": uuid.uuid4().hex,
        "generated_at": utc_iso(datetime.now(timezone.utc).timestamp()),
        "roots": list(args.root), "query": args.query,
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
