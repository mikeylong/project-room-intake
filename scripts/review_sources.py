#!/usr/bin/env python3
"""Record an explicit review decision without modifying original sources."""
import argparse
import json
import sys
from pathlib import Path

from intake_store import DECISIONS, record_review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    parser.add_argument("--scope", choices=("snapshot", "source"), required=True)
    parser.add_argument("--expected-sha256", help="SHA-256 from the exact candidate revision reviewed; required for snapshot scope")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--notes")
    parser.add_argument("--classification")
    args = parser.parse_args()
    if args.scope == "snapshot" and args.expected_sha256 is None:
        parser.error("--expected-sha256 is required with --scope snapshot")
    if args.scope == "source" and args.expected_sha256 is not None:
        parser.error("--expected-sha256 is only valid with --scope snapshot")
    expected = {"kind": "sha256", "value": args.expected_sha256} if args.expected_sha256 is not None else None
    try:
        decision = record_review(Path(args.room), args.source_id, args.decision, args.scope, args.actor, args.notes, args.classification,
                                 expected_revision=expected)
    except (ValueError, OSError) as exc:
        print(f"Review failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
