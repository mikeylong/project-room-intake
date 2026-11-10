"""Versioned room observations and durable human review decisions (stdlib only)."""
from __future__ import annotations

import copy
import csv
import fcntl
import io
import json
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "project-room-intake.candidates.v2"
REVIEW_SCHEMA_VERSION = "project-room-intake.reviews.v1"
ARTIFACTS = ("candidate_sources.json", "candidate_sources.csv", "scan_summary.md")
LEGACY_FIELDS = [
    "source_id", "source_type", "root", "path", "relative_path", "name", "extension",
    "mime_type", "size_bytes", "modified_at", "owner", "mode", "sha256", "hash_status",
    "duplicate_hint", "snippet_status", "snippet", "relevance_score", "relevance_reasons",
]
CSV_FIELDS = LEGACY_FIELDS + [
    "revision", "metadata_token", "provider", "version", "retrieval_status",
    "observation_status", "last_observed_at", "observation_scope", "review", "extensions",
]
CANDIDATE_FIELDS = set(CSV_FIELDS)
CSV_FIELDS = ["scan_id", "generation_id", "coverage_status"] + CSV_FIELDS
DECISIONS = {"approved", "excluded", "deferred"}
HASH_STATUSES = {"not_requested", "hashed", "too_large", "symlink_skipped", "unsupported_type", "error", "changed"}
SNIPPET_STATUSES = {"not_requested", "captured", "empty", "binary_skipped", "too_large", "symlink_skipped", "unsupported_type", "error", "changed", "disabled"}
RETRIEVAL_STATUSES = {"metadata_only", "content_read", "content_error", "not_retrieved", "retrieved", "retrieval_error"}
PAYLOAD_FIELDS = {"schema_version", "scan_id", "generated_at", "roots", "query", "filters", "content_access",
                  "coverage", "enrichment", "candidates", "exclusions", "review_history_count", "publication", "extensions"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Invalid room JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


@contextmanager
def _locked(room: Path):
    if ".." in room.parts:
        raise ValueError("Room paths must not contain parent-directory components")
    absolute = room.absolute()
    for component in reversed((absolute, *absolute.parents)):
        if component.is_symlink():
            raise ValueError("Room path must not traverse symlinks: " + str(component))
    room.mkdir(parents=True, exist_ok=True)
    state = room / ".intake"
    if state.is_symlink():
        raise ValueError(".intake must be a local directory, not a symlink")
    state.mkdir(exist_ok=True)
    descriptor = os.open(state / "room.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Room lock must be a regular file")
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _verified_revision(revision) -> bool:
    return (isinstance(revision, dict) and set(revision) == {"kind", "value"}
            and revision.get("kind") == "sha256"
            and isinstance(revision.get("value"), str)
            and re.fullmatch(r"[0-9a-f]{64}", revision["value"]) is not None)


def _choice(value, choices) -> bool:
    return isinstance(value, str) and value in choices


def validate_payload(payload: dict) -> None:
    """Validate the small operational contract without a JSON Schema dependency."""
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Expected candidate schema " + SCHEMA_VERSION)
    if set(payload) - PAYLOAD_FIELDS:
        raise ValueError("Unsupported candidate payload fields; preserve and migrate explicitly")
    if "extensions" in payload and not isinstance(payload["extensions"], dict):
        raise ValueError("Payload extensions must be a namespaced object")
    for field in ("scan_id", "generated_at"):
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise ValueError(f"Missing or invalid {field}")
    if not isinstance(payload.get("roots"), list) or not all(isinstance(v, str) for v in payload["roots"]):
        raise ValueError("roots must be a list of paths")
    if not isinstance(payload.get("query"), str):
        raise ValueError("query must be a string")
    filters = payload.get("filters", {})
    if not isinstance(filters, dict) or set(filters) != {"include", "exclude"} or any(not isinstance(filters.get(k), list) or not all(isinstance(v, str) for v in filters[k]) for k in ("include", "exclude")):
        raise ValueError("filters must contain include and exclude lists")
    access = payload.get("content_access", {})
    modes = {"metadata": (False, False), "hash": (True, False), "preview": (False, True), "hash+preview": (True, True)}
    if not isinstance(access, dict) or not _choice(access.get("mode"), modes) or any(not isinstance(access.get(k), bool) for k in ("hashes", "snippets")):
        raise ValueError("content_access must report mode, hashes, and snippets")
    if (access["hashes"], access["snippets"]) != modes[access["mode"]]:
        raise ValueError("Content-access mode contradicts selected enrichment")
    coverage = payload.get("coverage", {})
    if not isinstance(coverage, dict) or not _choice(coverage.get("status"), {"complete", "partial", "failed"}) or not isinstance(coverage.get("inputs"), list) or not isinstance(coverage.get("errors"), list):
        raise ValueError("Invalid discovery coverage")
    enrichment = payload.get("enrichment", {})
    if not isinstance(enrichment, dict) or not _choice(enrichment.get("status"), {"not_requested", "complete", "partial"}) or not isinstance(enrichment.get("errors"), list):
        raise ValueError("Invalid enrichment status")
    for error in coverage["errors"] + enrichment["errors"]:
        if not isinstance(error, dict) or any(not isinstance(error.get(k), str) for k in ("path", "operation", "error", "message")):
            raise ValueError("Errors require path, operation, error, and message")
    if not isinstance(payload.get("exclusions"), list) or not isinstance(payload.get("candidates"), list):
        raise ValueError("candidates and exclusions must be lists")
    seen = set()
    for candidate in payload["candidates"]:
        if not isinstance(candidate, dict) or any(not isinstance(candidate.get(k), str) or not candidate[k] for k in ("source_id", "source_type", "path")):
            raise ValueError("Candidates require source_id, source_type, and path")
        if candidate["source_id"] in seen:
            raise ValueError("Duplicate source_id: " + candidate["source_id"])
        seen.add(candidate["source_id"])
        if set(candidate) - CANDIDATE_FIELDS:
            raise ValueError("Unsupported candidate fields; preserve and migrate explicitly")
        if "extensions" in candidate and not isinstance(candidate["extensions"], dict):
            raise ValueError("Candidate extensions must be a namespaced object")
        if candidate.get("revision") is not None and not _verified_revision(candidate["revision"]):
            raise ValueError("Revision must be null or a verified SHA-256 value")
        if candidate.get("revision") is not None and (candidate.get("sha256") != candidate["revision"]["value"] or candidate.get("hash_status") != "hashed"):
            raise ValueError("Verified revision must match a successfully computed SHA-256")
        for field in ("size_bytes", "relevance_score", "revision", "metadata_token", "provider", "version", "retrieval_status"):
            if field not in candidate:
                raise ValueError("Candidate is missing " + field)
        if candidate["metadata_token"] is not None:
            token = candidate["metadata_token"]
            if not isinstance(token, dict) or any(not isinstance(token.get(k), int) or isinstance(token[k], bool) for k in ("size_bytes", "mtime_ns")) or token["size_bytes"] < 0:
                raise ValueError("metadata_token must contain integer size_bytes and mtime_ns")
        if any(candidate[k] is not None and not isinstance(candidate[k], str) for k in ("provider", "version")):
            raise ValueError("Provider and version must be strings or null")
        if not _choice(candidate["retrieval_status"], RETRIEVAL_STATUSES):
            raise ValueError("Unknown candidate retrieval status")
        for field, allowed in (("hash_status", HASH_STATUSES), ("snippet_status", SNIPPET_STATUSES)):
            if field in candidate and not _choice(candidate[field], allowed):
                raise ValueError("Unknown candidate " + field)
        for field in ("modified_at", "owner", "mode"):
            if field in candidate and candidate[field] is not None and not isinstance(candidate[field], str):
                raise ValueError(field + " must be text or null")
        if "observation_status" in candidate and not _choice(candidate["observation_status"], {"observed", "missing", "unobserved"}):
            raise ValueError("Invalid observation status")
        for field in ("size_bytes", "relevance_score"):
            if field == "size_bytes" and candidate.get(field) is None and candidate.get("provider") is not None:
                continue
            if not isinstance(candidate.get(field), int) or isinstance(candidate[field], bool) or candidate[field] < 0:
                raise ValueError(f"Candidate {field} must be a nonnegative integer")


def _load_reviews(room: Path) -> dict:
    path = room / "review_decisions.json"
    if path.is_symlink():
        raise ValueError("review_decisions.json must not be a symlink")
    if not path.exists():
        return {"schema_version": REVIEW_SCHEMA_VERSION, "decisions": []}
    reviews = _json(path)
    if set(reviews) != {"schema_version", "decisions"} or reviews["schema_version"] != REVIEW_SCHEMA_VERSION or not isinstance(reviews["decisions"], list):
        raise ValueError("Unsupported review record; preserve it and migrate explicitly")
    ids = set()
    required = {"decision_id", "source_id", "decision", "scope", "revision", "actor", "notes", "classification", "recorded_at"}
    for decision in reviews["decisions"]:
        if not isinstance(decision, dict) or set(decision) != required or not _choice(decision.get("decision"), DECISIONS) or not _choice(decision.get("scope"), {"snapshot", "source"}):
            raise ValueError("Invalid review decision")
        if any(not isinstance(decision.get(k), str) or not decision[k] for k in ("decision_id", "source_id", "actor", "recorded_at")):
            raise ValueError("Review identity, actor, and timestamp are required")
        if decision["decision_id"] in ids:
            raise ValueError("Duplicate review decision ID")
        ids.add(decision["decision_id"])
        if not isinstance(decision["notes"], str) or (decision["classification"] is not None and not isinstance(decision["classification"], str)):
            raise ValueError("Invalid review notes or classification")
        if decision["scope"] == "snapshot" and not _verified_revision(decision["revision"]):
            raise ValueError("Snapshot decisions require a verified revision")
        if decision["scope"] == "source" and decision["revision"] is not None:
            raise ValueError("Source-scoped decisions must have null revision")
    return reviews


def _current(room: Path) -> dict | None:
    pointer = room / ".intake" / "current"
    if not pointer.is_symlink():
        if pointer.exists():
            raise ValueError("Invalid room current pointer")
        return None
    target = pointer.resolve(strict=True)
    state = (room / ".intake").resolve()
    if target.parent != state / "generations" and target != state / "legacy-v1":
        raise ValueError("Room current pointer is outside managed generations")
    return _json(target / ARTIFACTS[0])


def load_current(room: Path) -> dict | None:
    """Load the committed observation set, without materializing source files."""
    return _current(Path(room))


def _write(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        _write(temporary, json.dumps(value, indent=2) + "\n")
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _switch_current(room: Path, target: str) -> None:
    temporary = room / ".intake" / (".current-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, room / ".intake" / "current")
        _sync_directory(room / ".intake")
    finally:
        temporary.unlink(missing_ok=True)


def _legacy(room: Path) -> dict | None:
    existing = [name for name in ARTIFACTS if (room / name).exists() or (room / name).is_symlink()]
    if not existing:
        return None
    if all((room / name).is_symlink() and os.readlink(room / name) == ".intake/current/" + name for name in existing):
        # An interrupted first publication can leave only these managed links.
        return None
    if set(existing) != set(ARTIFACTS) or any((room / name).is_symlink() for name in ARTIFACTS):
        raise ValueError("Incomplete or unsupported existing room; preserve and migrate explicitly")
    previous = _json(room / ARTIFACTS[0])
    expected = {"schema_version", "generated_at", "roots", "query", "candidates", "exclusions"}
    if set(previous) != expected or previous.get("schema_version") != "project-room-intake.candidates.v1":
        raise ValueError("Existing room has unknown/review extensions; explicit migration required")
    if not isinstance(previous["candidates"], list) or any(not isinstance(c, dict) or set(c) != set(LEGACY_FIELDS) for c in previous["candidates"]):
        raise ValueError("Legacy candidate extensions cannot be discarded; explicit migration required")
    with (room / "candidate_sources.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if reader.fieldnames != LEGACY_FIELDS or rows != [{k: str(c[k]) for k in LEGACY_FIELDS} for c in previous["candidates"]]:
            raise ValueError("Legacy CSV differs from JSON; preserve reviewed edits and migrate explicitly")
    return previous


def _ensure_layout(room: Path, previous: dict | None) -> None:
    state = room / ".intake"
    generations = state / "generations"
    if generations.is_symlink():
        raise ValueError("Generation directory must not be a symlink")
    generations.mkdir(exist_ok=True)
    if previous is not None and previous.get("schema_version") == "project-room-intake.candidates.v1" and not (state / "current").is_symlink():
        backup = state / "legacy-v1"
        if backup.exists():
            raise ValueError("Legacy backup already exists; inspect recovery state before migration")
        backup.mkdir()
        for name in ARTIFACTS:
            shutil.copy2(room / name, backup / name)
        _sync_directory(backup)
        _switch_current(room, "legacy-v1")
    for name in ARTIFACTS:
        destination = room / name
        target = ".intake/current/" + name
        if destination.is_symlink():
            if os.readlink(destination) != target:
                raise ValueError("Unexpected canonical artifact link: " + str(destination))
        elif destination.exists() and not (previous and previous.get("schema_version") == "project-room-intake.candidates.v1"):
            raise ValueError("Canonical artifact was replaced or edited: " + str(destination))
        else:
            if destination.exists() and destination.read_bytes() != (state / "legacy-v1" / name).read_bytes():
                raise ValueError("Legacy artifact changed during migration: " + str(destination))
            temporary = room / ("." + name + "." + uuid.uuid4().hex)
            try:
                temporary.symlink_to(target)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    _sync_directory(room)


def _scope(payload: dict) -> dict | None:
    if "filters" not in payload:
        return None
    return {"roots": sorted(set(payload["roots"])), "filters": {k: sorted(set(payload["filters"][k])) for k in ("include", "exclude")}}


def _preserve_extensions(previous: dict, incoming: dict) -> None:
    if "extensions" not in previous:
        return
    merged = copy.deepcopy(previous["extensions"])
    for key, value in incoming.get("extensions", {}).items():
        if key in merged and merged[key] != value:
            raise ValueError("Conflicting extension requires explicit migration: " + key)
        merged[key] = value
    incoming["extensions"] = merged


def _review(candidate: dict, latest: dict | None) -> dict:
    if latest is None:
        return {"status": "unreviewed", "reason": "no_decision", "decision_id": None}
    result = {k: latest[k] for k in ("decision_id", "scope", "actor", "notes", "classification", "recorded_at")}
    if candidate["observation_status"] != "observed":
        result.update(status="unavailable", reason=candidate["observation_status"])
    elif latest["scope"] == "source":
        result.update(status=latest["decision"], reason="source_scope")
    elif candidate.get("revision") is None:
        result.update(status="needs_review", reason="revision_unknown")
    elif candidate["revision"] != latest["revision"]:
        result.update(status="needs_review", reason="revision_changed")
    else:
        result.update(status=latest["decision"], reason="matching_revision")
    return result


def _csv(payload: dict) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for candidate in payload["candidates"]:
        row = {key: json.dumps(candidate[key], sort_keys=True) if isinstance(candidate.get(key), (dict, list)) else candidate.get(key) for key in CSV_FIELDS}
        row.update(scan_id=payload["scan_id"], generation_id=payload["publication"]["generation_id"], coverage_status=payload["coverage"]["status"])
        writer.writerow(row)
    return buffer.getvalue()


def _summary(payload: dict) -> str:
    observed = sum(c["observation_status"] == "observed" for c in payload["candidates"])
    lines = ["# Scan Summary", "", f"- Scan: {payload['scan_id']}", f"- Generated: {payload['generated_at']}",
             f"- Coverage: {payload['coverage']['status']}", f"- Content access: {payload['content_access']['mode']}",
             f"- Observed candidates: {observed}", f"- Retained missing/unobserved: {len(payload['candidates']) - observed}",
             f"- Duplicate hints: {sum(bool(c.get('duplicate_hint')) for c in payload['candidates'] if c['observation_status'] == 'observed')}",
             f"- Excluded entries: {len(payload['exclusions'])}", f"- Coverage errors: {len(payload['coverage']['errors'])}",
             f"- Enrichment: {payload['enrichment']['status']}", "", "JSON is authoritative; CSV is a generated review/export view.",
             "Review history is stored separately in review_decisions.json.", "", "## Input outcomes", ""]
    lines.extend("- " + json.dumps(item, sort_keys=True) for item in payload["coverage"]["inputs"])
    lines.extend(["", "## Discovery errors", ""])
    lines.extend("- " + json.dumps(item, sort_keys=True) for item in payload["coverage"]["errors"])
    if not payload["coverage"]["errors"]:
        lines.append("- none")
    lines.extend(["", "## Enrichment errors", ""])
    lines.extend("- " + json.dumps(item, sort_keys=True) for item in payload["enrichment"]["errors"])
    if not payload["enrichment"]["errors"]:
        lines.append("- none")
    lines.extend(["", "## Top candidate matches", ""])
    top = sorted((c for c in payload["candidates"] if c["observation_status"] == "observed"), key=lambda c: (-c["relevance_score"], c["path"]))[:10]
    lines.extend(f"- score {c['relevance_score']}: {json.dumps(c.get('relative_path') or c['path'])} {c.get('relevance_reasons', '')}".rstrip() for c in top)
    if not top:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _publish(room: Path, payload: dict, reviews: dict, preserve_observations: bool = False) -> dict:
    validate_payload(payload)
    previous = _current(room)
    if previous is None:
        previous = _legacy(room)
    elif previous.get("schema_version") == SCHEMA_VERSION:
        validate_payload(previous)
    elif previous.get("schema_version") != "project-room-intake.candidates.v1":
        raise ValueError("Unsupported current room schema")
    result = copy.deepcopy(payload)
    if previous:
        _preserve_extensions(previous, result)
    scope = _scope(result)
    if not preserve_observations:
        prior_candidates = {c["source_id"]: c for c in previous.get("candidates", [])} if previous else {}
        for candidate in result["candidates"]:
            if candidate["source_id"] in prior_candidates:
                _preserve_extensions(prior_candidates[candidate["source_id"]], candidate)
            candidate.update(observation_status="observed", last_observed_at=result["generated_at"], observation_scope=scope)
        current_ids = {c["source_id"] for c in result["candidates"]}
        for original in previous.get("candidates", []) if previous else []:
            if original["source_id"] in current_ids:
                continue
            retained = copy.deepcopy(original)
            if retained["size_bytes"] is not None:
                retained["size_bytes"] = int(retained["size_bytes"])
            retained["relevance_score"] = int(retained["relevance_score"])
            retained.setdefault("revision", None)
            retained.setdefault("metadata_token", None)
            retained.setdefault("provider", None)
            retained.setdefault("version", None)
            legacy_retrieval = "content_read" if retained.get("hash_status") == "hashed" or retained.get("snippet_status") == "captured" else "content_error" if retained.get("hash_status") == "error" or retained.get("snippet_status") == "error" else "metadata_only"
            retained.setdefault("retrieval_status", legacy_retrieval)
            retained.setdefault("observation_scope", _scope(previous))
            retained.setdefault("last_observed_at", previous["generated_at"])
            retained["observation_status"] = "missing" if result["coverage"]["status"] == "complete" and retained["observation_scope"] == scope else "unobserved"
            result["candidates"].append(retained)
    latest = {d["source_id"]: d for d in reviews["decisions"]}
    for candidate in result["candidates"]:
        candidate["review"] = _review(candidate, latest.get(candidate["source_id"]))
    result["review_history_count"] = len(reviews["decisions"])
    result["publication"] = {"generation_id": uuid.uuid4().hex, "published_at": _now()}
    validate_payload(result)
    _ensure_layout(room, previous)
    generation = room / ".intake" / "generations" / result["publication"]["generation_id"]
    generation.mkdir()
    _write(generation / ARTIFACTS[0], json.dumps(result, indent=2) + "\n")
    _write(generation / ARTIFACTS[1], _csv(result))
    _write(generation / ARTIFACTS[2], _summary(result))
    _sync_directory(generation)
    _sync_directory(generation.parent)
    _switch_current(room, "generations/" + generation.name)
    return result


def publish_room(room: Path, payload: dict) -> dict:
    """Commit observations and joined review status; return the published payload."""
    room = Path(room)
    with _locked(room):
        reviews = _load_reviews(room)
        result = _publish(room, payload, reviews)
        if not (room / "review_decisions.json").exists():
            _atomic_json(room / "review_decisions.json", reviews)
        return result


def record_review(room: Path, source_id: str, decision: str, scope: str, actor: str,
                  notes: str | None = None, classification: str | None = None) -> dict:
    """Append an explicit decision and republish its generated candidate view."""
    if not _choice(decision, DECISIONS) or not _choice(scope, {"snapshot", "source"}) or not isinstance(actor, str) or not actor.strip():
        raise ValueError("Explicit decision, scope, and nonempty actor are required")
    if notes is not None and not isinstance(notes, str) or classification is not None and not isinstance(classification, str):
        raise ValueError("Review notes and classification must be text")
    room = Path(room)
    with _locked(room):
        current = _current(room)
        if current is None or current.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Publish a v2 room before recording a review")
        validate_payload(current)
        candidate = next((c for c in current["candidates"] if c["source_id"] == source_id), None)
        if candidate is None or candidate["observation_status"] != "observed":
            raise ValueError("Review requires a currently observed source")
        if scope == "snapshot" and not _verified_revision(candidate.get("revision")):
            raise ValueError("Snapshot review requires a verified revision; explicitly use source scope only when intended")
        reviews = _load_reviews(room)
        prior = next((d for d in reversed(reviews["decisions"]) if d["source_id"] == source_id), {})
        entry = {"decision_id": uuid.uuid4().hex, "source_id": source_id, "decision": decision, "scope": scope,
                 "revision": candidate["revision"] if scope == "snapshot" else None, "actor": actor,
                 "notes": notes if notes is not None else prior.get("notes", ""),
                 "classification": classification if classification is not None else prior.get("classification"), "recorded_at": _now()}
        reviews["decisions"].append(entry)
        _atomic_json(room / "review_decisions.json", reviews)
        # The history is authoritative. If publication fails, retrying a scan joins it;
        # the previous committed generation remains recoverable and untouched.
        _publish(room, current, reviews, preserve_observations=True)
        return entry
