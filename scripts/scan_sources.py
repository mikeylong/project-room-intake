#!/usr/bin/env python3
"""Scan approved local roots and write project-room candidate source artifacts."""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import mimetypes
import os
import pwd
import stat
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "project-room-intake.candidates.v1"
DEFAULT_EXCLUDES = [
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".DS_Store",
    ".cache",
    ".venv",
    "venv",
    "dist",
    "build",
]
CSV_FIELDS = [
    "source_id",
    "source_type",
    "root",
    "path",
    "relative_path",
    "name",
    "extension",
    "mime_type",
    "size_bytes",
    "modified_at",
    "owner",
    "mode",
    "sha256",
    "hash_status",
    "duplicate_hint",
    "snippet_status",
    "snippet",
    "relevance_score",
    "relevance_reasons",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan explicitly approved local roots for project-room candidate sources."
    )
    parser.add_argument("--root", action="append", required=True, help="Approved local root to scan.")
    parser.add_argument("--room", required=True, help="Room directory for output artifacts.")
    parser.add_argument("--query", default="", help="Optional relevance hint.")
    parser.add_argument("--include-glob", action="append", default=[], help="Include only matching relative paths. Repeatable.")
    parser.add_argument("--exclude-glob", action="append", default=[], help="Exclude matching relative paths. Repeatable.")
    parser.add_argument("--hash-max-mb", type=float, default=50.0, help="Maximum file size to hash.")
    parser.add_argument(
        "--snippets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture short text snippets when files look text-like.",
    )
    return parser.parse_args()


def utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def normalize_rel(path: Path) -> str:
    return path.as_posix()


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in path.parts)


def matches_any(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern) for pattern in patterns)


def excluded_reason(rel: str, path: Path, user_excludes: list[str]) -> str | None:
    if is_hidden(path):
        return "hidden"
    if matches_any(rel, DEFAULT_EXCLUDES):
        return "default_exclude"
    if matches_any(rel, user_excludes):
        return "user_exclude"
    return None


def included(rel: str, includes: list[str]) -> bool:
    return not includes or matches_any(rel, includes)


def source_id(source_type: str, path: str) -> str:
    digest = hashlib.sha1(f"{source_type}:{path}".encode("utf-8")).hexdigest()[:16]
    return f"src_{digest}"


def hash_file(path: Path, size: int, limit_bytes: int, is_link: bool) -> tuple[str, str]:
    if is_link:
        return "", "symlink_skipped"
    if size > limit_bytes:
        return "", "too_large"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), "hashed"
    except OSError:
        return "", "error"


def text_snippet(path: Path, enabled: bool, size: int, is_link: bool) -> tuple[str, str]:
    if not enabled:
        return "", "disabled"
    if is_link:
        return "", "symlink_skipped"
    if size > 5 * 1024 * 1024:
        return "", "too_large"
    try:
        data = path.read_bytes()[:8192]
    except OSError:
        return "", "error"
    if b"\x00" in data:
        return "", "binary_skipped"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError:
            return "", "binary_skipped"
    snippet = " ".join(text.split())[:600]
    return snippet, "captured" if snippet else "empty"


def query_terms(query: str) -> list[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in query).split()
    return sorted({term for term in raw if len(term) >= 3})


def relevance(record: dict[str, str], terms: list[str]) -> tuple[int, str]:
    if not terms:
        return 0, ""
    name = record["name"].lower()
    rel = record["relative_path"].lower()
    snippet = record["snippet"].lower()
    score = 0
    reasons: list[str] = []
    for term in terms:
        if term in name:
            score += 3
            reasons.append(f"name:{term}")
        elif term in rel:
            score += 2
            reasons.append(f"path:{term}")
        elif term in snippet:
            score += 1
            reasons.append(f"snippet:{term}")
    return score, ";".join(reasons)


def scan_root(root: Path, args: argparse.Namespace, terms: list[str], exclusions: list[dict[str, str]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    limit_bytes = int(args.hash_max_mb * 1024 * 1024)
    root = root.expanduser().resolve()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            child = current / dirname
            rel = normalize_rel(child.relative_to(root))
            reason = excluded_reason(rel, child.relative_to(root), args.exclude_glob)
            if reason:
                exclusions.append({"root": str(root), "path": str(child), "relative_path": rel, "reason": reason, "kind": "directory"})
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            path = current / filename
            rel = normalize_rel(path.relative_to(root))
            relative_path = path.relative_to(root)
            reason = excluded_reason(rel, relative_path, args.exclude_glob)
            if reason:
                exclusions.append({"root": str(root), "path": str(path), "relative_path": rel, "reason": reason, "kind": "file"})
                continue
            if not included(rel, args.include_glob):
                exclusions.append({"root": str(root), "path": str(path), "relative_path": rel, "reason": "not_included", "kind": "file"})
                continue

            try:
                info = path.lstat()
            except OSError as exc:
                exclusions.append({"root": str(root), "path": str(path), "relative_path": rel, "reason": f"stat_error:{exc.__class__.__name__}", "kind": "file"})
                continue

            is_link = path.is_symlink()
            source_type = "local_symlink" if is_link else "local_file"
            mime_type = mimetypes.guess_type(path.name)[0] or ""
            sha256, hash_status = hash_file(path, info.st_size, limit_bytes, is_link)
            snippet, snippet_status = text_snippet(path, args.snippets, info.st_size, is_link)
            record = {
                "source_id": source_id(source_type, str(path)),
                "source_type": source_type,
                "root": str(root),
                "path": str(path),
                "relative_path": rel,
                "name": path.name,
                "extension": path.suffix.lower().lstrip("."),
                "mime_type": mime_type,
                "size_bytes": str(info.st_size),
                "modified_at": utc_iso(info.st_mtime),
                "owner": owner_name(info.st_uid),
                "mode": stat.filemode(info.st_mode),
                "sha256": sha256,
                "hash_status": hash_status,
                "duplicate_hint": "",
                "snippet_status": snippet_status,
                "snippet": snippet,
                "relevance_score": "0",
                "relevance_reasons": "",
            }
            score, reasons = relevance(record, terms)
            record["relevance_score"] = str(score)
            record["relevance_reasons"] = reasons
            records.append(record)

    return records


def add_duplicate_hints(records: list[dict[str, str]]) -> None:
    by_hash: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_size: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        if record["sha256"]:
            by_hash[record["sha256"]].append(record)
        else:
            by_size[record["size_bytes"]].append(record)
    for digest, group in by_hash.items():
        if len(group) > 1:
            for record in group:
                record["duplicate_hint"] = f"same_sha256:{digest[:12]}"
    for size, group in by_size.items():
        if size != "0" and len(group) > 1:
            for record in group:
                if not record["duplicate_hint"]:
                    record["duplicate_hint"] = f"same_size:{size}"


def write_outputs(room: Path, args: argparse.Namespace, records: list[dict[str, str]], exclusions: list[dict[str, str]]) -> None:
    room.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    csv_path = room / "candidate_sources.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "roots": [str(Path(root).expanduser().resolve()) for root in args.root],
        "query": args.query,
        "candidates": records,
        "exclusions": exclusions,
    }
    (room / "candidate_sources.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    exclusion_counts = Counter(item["reason"] for item in exclusions)
    duplicate_count = sum(1 for record in records if record["duplicate_hint"])
    top = sorted(records, key=lambda item: (-int(item["relevance_score"]), item["relative_path"]))[:10]
    lines = [
        "# Scan Summary",
        "",
        f"- Generated: {generated_at}",
        f"- Roots: {len(args.root)}",
        f"- Candidates: {len(records)}",
        f"- Exclusions: {len(exclusions)}",
        f"- Duplicate hints: {duplicate_count}",
        f"- Snippets: {'enabled' if args.snippets else 'disabled'}",
        "",
        "## Exclusion Summary",
        "",
    ]
    if exclusion_counts:
        lines.extend(f"- {reason}: {count}" for reason, count in sorted(exclusion_counts.items()))
    else:
        lines.append("- none")
    lines.extend(["", "## Top Candidate Matches", ""])
    if top:
        lines.extend(
            f"- score {record['relevance_score']}: `{record['relative_path']}` {record['relevance_reasons']}".rstrip()
            for record in top
        )
    else:
        lines.append("- none")
    (room / "scan_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    terms = query_terms(args.query)
    records: list[dict[str, str]] = []
    exclusions: list[dict[str, str]] = []

    for raw_root in args.root:
        root = Path(raw_root).expanduser()
        if not root.exists() or not root.is_dir():
            exclusions.append({"root": str(root), "path": str(root), "relative_path": "", "reason": "root_unavailable", "kind": "root"})
            continue
        records.extend(scan_root(root, args, terms, exclusions))

    add_duplicate_hints(records)
    write_outputs(Path(args.room).expanduser(), args, records, exclusions)
    print(f"Wrote {len(records)} candidates and {len(exclusions)} exclusions to {Path(args.room).expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
