#!/usr/bin/env python3
"""Freeze bounded trial criteria and score two explicitly recorded observations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path


PROTOCOL_VERSION = "project-room-intake.trial.v1"
FREEZE_VERSION = "project-room-intake.trial-freeze.v1"
OBSERVATION_VERSION = "project-room-intake.trial-observation.v1"
RESULT_VERSION = "project-room-intake.trial-result.v1"
SCALAR_METRICS = ("user_corrections", "clarification_turns", "elapsed_seconds", "human_effort_minutes")
SET_METRICS = ("missed_important_sources", "irrelevant_candidates", "version_mistakes", "unverified_versions")


def _object(value, fields: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} has missing or unsupported fields")


def _text(value, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")


def _texts(value, label: str, unique: bool = False) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    for item in value:
        _text(item, label + " entry")
    if unique and len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicate IDs")


def _hash(value, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 value")


def validate_protocol(protocol: dict) -> None:
    _object(protocol, {"schema_version", "trial_id", "task", "corpus_revision", "arms", "sources", "confounders", "measurement_notes"}, "protocol")
    if protocol["schema_version"] != PROTOCOL_VERSION:
        raise ValueError("Unsupported trial protocol version")
    for field in ("trial_id", "task", "corpus_revision", "measurement_notes"):
        _text(protocol[field], field)
    _texts(protocol["arms"], "arms", unique=True)
    if len(protocol["arms"]) != 2:
        raise ValueError("A trial requires exactly two ordered arms")
    _texts(protocol["confounders"], "confounders")
    if not isinstance(protocol["sources"], list) or not protocol["sources"]:
        raise ValueError("sources must be a nonempty list")
    seen = set()
    for source in protocol["sources"]:
        _object(source, {"source_id", "sha256", "important", "relevant"}, "protocol source")
        _text(source["source_id"], "source_id")
        _hash(source["sha256"], "source sha256")
        if source["source_id"] in seen:
            raise ValueError("Duplicate protocol source_id")
        seen.add(source["source_id"])
        if type(source["important"]) is not bool or type(source["relevant"]) is not bool:
            raise ValueError("Source important/relevant flags must be booleans")
        if source["important"] and not source["relevant"]:
            raise ValueError("An important source must also be relevant")


def protocol_sha256(protocol: dict) -> str:
    """Hash canonical JSON, preserving list order as part of the frozen criteria."""
    validate_protocol(protocol)
    serialized = json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def freeze_protocol(protocol: dict) -> dict:
    return {"schema_version": FREEZE_VERSION, "protocol_sha256": protocol_sha256(protocol), "protocol": copy.deepcopy(protocol)}


def validate_frozen(frozen: dict) -> dict:
    _object(frozen, {"schema_version", "protocol_sha256", "protocol"}, "frozen protocol")
    if frozen["schema_version"] != FREEZE_VERSION:
        raise ValueError("Unsupported frozen protocol version")
    _hash(frozen["protocol_sha256"], "protocol_sha256")
    if protocol_sha256(frozen["protocol"]) != frozen["protocol_sha256"]:
        raise ValueError("Frozen protocol digest does not match its criteria")
    return frozen["protocol"]


def validate_observation(observation: dict, frozen: dict) -> None:
    fields = {"schema_version", "trial_id", "protocol_sha256", "arm_id", "candidate_source_ids", "used_sources", "notes", *SCALAR_METRICS}
    _object(observation, fields, "observation")
    protocol = frozen["protocol"]
    if observation["schema_version"] != OBSERVATION_VERSION:
        raise ValueError("Unsupported trial observation version")
    if observation["trial_id"] != protocol["trial_id"] or observation["protocol_sha256"] != frozen["protocol_sha256"]:
        raise ValueError("Observation does not identify this frozen trial")
    if observation["arm_id"] not in protocol["arms"]:
        raise ValueError("Unknown observation arm_id")
    _texts(observation["candidate_source_ids"], "candidate_source_ids", unique=True)
    candidates = set(observation["candidate_source_ids"])
    known = {source["source_id"] for source in protocol["sources"]}
    if not candidates <= known:
        raise ValueError("Observation has an unknown candidate source_id")
    if not isinstance(observation["used_sources"], list):
        raise ValueError("used_sources must be a list")
    seen = set()
    for source in observation["used_sources"]:
        _object(source, {"source_id", "sha256"}, "used source")
        _text(source["source_id"], "used source_id")
        if source["source_id"] in seen:
            raise ValueError("Duplicate used source_id")
        seen.add(source["source_id"])
        if source["source_id"] not in candidates:
            raise ValueError("Every used source must be a declared candidate")
        if source["sha256"] is not None:
            _hash(source["sha256"], "used source sha256")
    for field in SCALAR_METRICS:
        value = observation[field]
        if value is None:
            continue
        if field in ("user_corrections", "clarification_turns"):
            valid = type(value) is int and value >= 0
        else:
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
        if not valid:
            raise ValueError(f"{field} must be a nonnegative {'integer' if field in SCALAR_METRICS[:2] else 'finite number'} or null")
    _texts(observation["notes"], "notes")


def _source_set(source_ids: set[str]) -> dict:
    return {"count": len(source_ids), "source_ids": sorted(source_ids)}


def score_trial(frozen: dict, observations: list[dict]) -> dict:
    protocol = validate_frozen(frozen)
    if not isinstance(observations, list) or len(observations) != 2:
        raise ValueError("Supply exactly one observation for each of the two arms")
    by_arm = {}
    for observation in observations:
        validate_observation(observation, frozen)
        if observation["arm_id"] in by_arm:
            raise ValueError("Duplicate observation arm_id")
        by_arm[observation["arm_id"]] = observation
    expected = {source["source_id"]: source for source in protocol["sources"]}
    important = {key for key, source in expected.items() if source["important"]}
    relevant = {key for key, source in expected.items() if source["relevant"]}
    arms = []
    for arm_id in protocol["arms"]:
        observation = by_arm[arm_id]
        used = {source["source_id"]: source["sha256"] for source in observation["used_sources"]}
        metrics = {
            "missed_important_sources": _source_set(important - used.keys()),
            "irrelevant_candidates": _source_set(set(observation["candidate_source_ids"]) - relevant),
            "version_mistakes": _source_set({key for key, digest in used.items() if digest is not None and digest != expected[key]["sha256"]}),
            "unverified_versions": _source_set({key for key, digest in used.items() if digest is None}),
            **{field: observation[field] for field in SCALAR_METRICS},
        }
        arms.append({"arm_id": arm_id, "metrics": metrics, "notes": list(observation["notes"])})
    deltas = {}
    for field in (*SET_METRICS, *SCALAR_METRICS):
        values = [arm["metrics"][field]["count"] if field in SET_METRICS else arm["metrics"][field] for arm in arms]
        deltas[field] = None if None in values else values[1] - values[0]
    return {
        "schema_version": RESULT_VERSION, "trial_id": protocol["trial_id"],
        "protocol_sha256": frozen["protocol_sha256"], "corpus_revision": protocol["corpus_revision"],
        "comparison_order": {"first": protocol["arms"][0], "second": protocol["arms"][1], "delta": "second_minus_first"},
        "arms": arms, "deltas": deltas, "confounders": list(protocol["confounders"]),
        "measurement_notes": protocol["measurement_notes"],
    }


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def _invalid_constant(value: str):
    raise ValueError("Nonfinite JSON value: " + value)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "score"):
        command = commands.add_parser(name)
        command.add_argument("--protocol", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True, help="New output file; existing files are never overwritten.")
        if name == "score":
            command.add_argument("--observation", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        protocol = _read_json(args.protocol)
        result = freeze_protocol(protocol) if args.command == "freeze" else score_trial(protocol, [_read_json(path) for path in args.observation])
        content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(f"Trial not written: {exc}", file=sys.stderr)
        return 1
    print(f"{args.command.capitalize()} complete: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
