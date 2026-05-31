# Project Room Intake Contract

This reference defines stable artifacts for project/data-room intake. The room is a reviewable evidence workspace, not a hidden context dump.

## Candidate Discovery Artifacts

`scripts/scan_sources.py` writes these files to the room directory:

- `candidate_sources.csv`: Spreadsheet-friendly candidate source table.
- `candidate_sources.json`: Machine-readable scan payload with `schema_version`, `generated_at`, `roots`, `query`, `candidates`, and `exclusions`.
- `scan_summary.md`: Human summary of scanned roots, candidate count, exclusions, duplicate hints, and top relevance matches.

Candidate source fields:

- `source_id`: Stable ID derived from source type and local path or external URI.
- `source_type`: `local_file`, `local_symlink`, or manifest-defined connector/blob type.
- `root`: Approved scan root or manifest source.
- `path`: Absolute local path, or stable external URI/key for manifest rows.
- `relative_path`: Path relative to its approved root when local.
- `name`: Basename or object name.
- `extension`: Lowercase extension without the leading dot.
- `mime_type`: Best-effort MIME type.
- `size_bytes`: Source size when known.
- `modified_at`: UTC ISO-8601 modified timestamp when known.
- `owner`: Local owner or provider owner when known.
- `mode`: Local permissions string when known.
- `sha256`: Content hash when hashed.
- `hash_status`: `hashed`, `too_large`, `symlink_skipped`, or `error`.
- `duplicate_hint`: Empty, `same_sha256:<prefix>`, or `same_size:<bytes>`.
- `snippet_status`: `captured`, `disabled`, `binary_skipped`, `symlink_skipped`, `too_large`, `empty`, or `error`.
- `snippet`: Short text preview only when snippets are enabled and safe to capture.
- `relevance_score`: Deterministic heuristic score from query terms.
- `relevance_reasons`: Semicolon-separated explanation for score.

## Room Artifacts After Approval

Create these only after the user approves candidate sources or gives an explicit materialization request:

- `source_inventory.md`: Reviewed source table with authority, currentness, use, limitations, and supported claims.
- `conflict_log.md`: Known source disagreements, affected claims, likely resolution paths, and owner/user decision.
- `missing_context.md`: Missing files, missing decisions, unsourced numbers, ambiguous assumptions, and whether each blocks drafting.
- `duplicate_report.md`: Duplicate and version-family findings with confidence and recommended human decision.
- `provenance_ledger.md`: When each source was found, approved, copied/symlinked/downloaded/referenced, and by which command or connector.
- `PROJECT_BRIEF.md`: Goal, audience, deliverable, approved source set, explicit authority decisions, constraints, and open questions.

## Materialization Defaults

Use references first:

- Local files: record source paths in the inventory and ledger.
- Blob/object stores: record provider, bucket/container, key, version/etag, and retrieval time.
- Connectors: record provider, object ID, URL, timestamp, and permissions evidence when available.

Copy, symlink, or download only when the user approves it or when parsing requires a local artifact. Record every materialization action in `provenance_ledger.md`.

## Review Gates

Gate 1: candidate discovery complete. Stop and ask which sources to approve, exclude, or re-scan.

Gate 2: room artifacts complete. Stop and ask whether to draft, research missing context, or revise source authority decisions.
