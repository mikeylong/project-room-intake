# Project Room Intake Contract

This reference defines stable artifacts for project/data-room intake. The room is a reviewable evidence workspace, not a hidden context dump.

## Candidate Discovery Artifacts

`scripts/scan_sources.py` publishes these artifacts for approved local directory roots:

- `candidate_sources.csv`: Spreadsheet-friendly candidate source table.
- `candidate_sources.json`: Authoritative machine-readable observations in `project-room-intake.candidates.v2`, including input scope, content-access mode, discovery coverage, errors, candidates, exclusions, and joined review status.
- `scan_summary.md`: Human summary of coverage, input outcomes, errors, candidate count, exclusions, duplicate hints, and top relevance matches.
- `review_decisions.json`: Durable decisions and history in `project-room-intake.reviews.v1`, separate from generated source observations.

Each CSV row includes `scan_id`, `generation_id`, and `coverage_status`; an empty CSV still requires the JSON/summary for coverage.

The machine contracts are [candidates.v2.schema.json](schemas/candidates.v2.schema.json) and [reviews.v1.schema.json](schemas/reviews.v1.schema.json). JSON is the authoritative structured record; CSV is a review/export view, not the place to store approval edits. The scanner does not import connector manifests or CSV edits.

Candidate observations retain these source fields:

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
- `hash_status`: Whether content was hashed, not requested, skipped, or failed; see the machine schema for supported values. An absent hash is not proof of unique content.
- `duplicate_hint`: Empty, `same_sha256:<prefix>`, or `same_size:<bytes>`.
- `snippet_status`: Whether a preview was captured, disabled, empty, skipped, or failed; see the machine schema for supported values.
- `snippet`: Short text preview only when snippets are enabled and safe to capture.
- `relevance_score`: Deterministic heuristic score from query terms.
- `relevance_reasons`: Semicolon-separated explanation for score.

Additional candidate fields are:

| Field | Meaning |
| --- | --- |
| `revision` | `null` when content revision is unknown, or `{ "kind": "sha256", "value": "<64 lowercase hexadecimal characters>" }` for a verified content revision |
| `metadata_token` | Metadata observation used to notice changes; never proof of identical content |
| `provider`, `version`, `retrieval_status` | Provider/retrieval metadata; unknown values must not be fabricated |
| `observation_status` | `observed`, `missing`, or `unobserved` |
| `last_observed_at`, `observation_scope` | Last successful observation and the scan scope associated with it |
| `review` | Joined decision status/reason and decision provenance; not a replacement for the review record |
| `extensions` | Additional metadata kept separate from the shared fields |

Revision evidence and the joined review state keep observations separate from authorization. `review.status` can be `unreviewed`, `approved`, `excluded`, `deferred`, `needs_review`, or `unavailable`; `review.reason` explains its applicability. A classification describes relevance or use; it does not approve a source. A prior decision remains in history even when its scope does not apply to the current observation.

## Content Access and Coverage

The local scanner is metadata-only by default. It may inspect filesystem metadata but must not open source contents to hash, preview, or parse them. `--metadata-only` makes the mode explicit and conflicts with `--hash-content` and `--snippets`. Those two flags independently request content reads and require authorization for their use. `--no-snippets` only disables previews; it does not disable an explicitly requested hash.

Metadata-only relevance uses names and paths. Local size and modification time cannot establish content equality or a known snapshot revision. Exact provider content identities, when available as metadata, can support duplicate hints if their semantics and provenance are recorded; an arbitrary etag or object ID is not automatically a content checksum.

Roots must be directories without symlink components. Name root and room directories directly; paths containing `..` are rejected. Source entry symlinks are inventoried without reading targets. The room and source roots must not overlap. Include globs narrow the discovered set and do not override hidden/default exclusions. An individually approved file must be handled as a separate reference; the scanner has no `--file` route, and file approval does not authorize enumerating its parent.

Coverage distinguishes:

| State | Meaning |
| --- | --- |
| `complete` | Every approved directory input was inspected under the declared filtering policy, including an accessible empty directory |
| `partial` | Some inputs or entries could be inspected, while discovery errors prevented complete coverage |
| `failed` | No approved input could be inspected |

Per-input outcomes and structured errors explain coverage. Deliberate exclusions do not themselves make a scan partial. Optional hash or preview errors are enrichment failures and do not themselves mean directory discovery failed. Do not describe a partial result as exhaustive or silently mark an unobserved source missing.

Exit codes: `0` means complete discovery with published artifacts; `3` means partial discovery with evidence published; `4` means failed discovery with evidence published; `5` means room compatibility or output publication failed; `2` means invalid command-line arguments. A nonzero coverage result can still contain useful candidates. An output failure is not a valid new scan result.

## Durable Review Decisions

Use `scripts/review_sources.py` to record explicit user decisions. It requires `--room`, `--source-id`, `--decision approved|excluded|deferred`, `--scope snapshot|source`, and `--actor`; `--notes` and `--classification` are optional free text. Snapshot scope additionally requires `--expected-sha256 HASH`; that flag is rejected with source scope. It records the decision; it does not obtain authorization from the user or grant itself permission to read sources. The source must be present as currently observed in a published v2 room.

Each decision records `decision_id`, `source_id`, `decision`, `scope`, `revision`, `actor`, `notes`, `classification`, and `recorded_at`. Decisions append to history. Omitted notes and classification retain the earlier values for that source. The CLI joins the relevant decision to generated observations without replacing the original approval record.

- **Snapshot scope** applies only to the known, verified content revision selected by the user. Every snapshot-scoped decision, including exclusion or deferral, requires an expected SHA-256 from the exact manifest/version reviewed and a matching verified current revision. The API receives that reviewed `expected_revision` as `{ "kind": "sha256", "value": "<64 lowercase hexadecimal characters>" }`; the CLI receives its value through `--expected-sha256`. It compares the expected and current revisions under the room lock before saving the decision. Missing or mismatched expectations fail. This prevents a decision made for version A from attaching to version B after a rescan. Do not replace the reviewed expectation with a freshly discovered hash. A size/mtime match is insufficient, and metadata-only discovery must not hash content to satisfy this requirement. Changed or unknown revisions do not silently inherit snapshot approval.
- **Source scope** explicitly applies across current and future observations of that source. Use it only when the user authorizes that broader scope; do not substitute it merely because a snapshot cannot be verified.
- **Unobserved versus missing:** a complete scan of the same source scope can establish that a prior source is missing. Partial coverage or a changed scan scope cannot. Preserve earlier decisions and notes in either case.

Source approval and drafting approval are different decisions. Record the latter in `PROJECT_BRIEF.md` and `provenance_ledger.md`; a source decision does not authorize the final deliverable.

The review CLI returns `0` after a successful decision/view update, `1` for a room/write error or a malformed or mismatched expected revision, and `2` for invalid arguments, including a missing snapshot hash or a hash supplied with source scope. A view-publication error can occur after the decision is saved; inspect the authoritative review record before retrying.

## Publication and Legacy Rooms

The scanner publishes candidate JSON, CSV, and summary as a single generation. Stable artifact paths resolve through `.intake/current` to an immutable generation. The durable review record is protected with room updates so a rescan cannot overwrite an intervening decision. Do not edit generated candidate artifacts to store decisions; use the review CLI.

Review history and its generated view do not form one atomic publication. A review command saves `review_decisions.json` first, then refreshes the candidate generation. If that refresh fails, the decision persists, the previous generation stays readable, and a subsequent successful scan repairs the joined view. Treat the review record as authoritative during recovery.

An interrupted publication must leave the last published generation usable. The six separately maintained room Markdown files are not regenerated by the scanner. Keep their reviewed content and provenance when updating source observations.

The supported v1 conversion preserves original artifacts under `.intake/legacy-v1/` before publishing v2. It accepts the known scanner format only when candidate CSV and JSON agree. Unknown extensions or inconsistent candidate edits stop conversion with an actionable error; they are not silently discarded. The check cannot detect coordinated edits to both candidate files. Legacy summary notes are retained in the backup rather than merged into a newly generated summary. Backups preserve legacy material but do not invent past approvals. Review the preserved originals and record explicit decisions through the review CLI.

Connector manifests remain manually prepared references. Record stable URI/key, provider, version or etag with its meaning, size, source modification time, owner when available, and retrieval status. Keep unknown modification times unknown; retrieval time is a different event. This schema work does not add a connector execution framework or authorize downloading objects.

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

After a source is approved, use a reference by default. Create a local parsing copy only when needed, and record the copy in `provenance_ledger.md`. Parsing convenience does not authorize an unapproved source. Record retrieval and materialization separately from source modification time.

## Review Gates

Gate 1: present the discovered candidates, coverage limits, proposed use, exclusions, and meaningful gaps. Obtain source approval and record its source/version scope. If that authorization is already explicit, reuse it; retrieval and room preparation within the approved scope do not need repeat confirmation.

Gate 2: present the completed room. Obtain approval to draft the intended deliverable, research a named gap, or revise authority. Once drafting is authorized, proceed within that scope. Honor and record explicit user instructions that already authorize the step or override the default procedure.

Ask only for genuinely missing information, bundling questions where useful. Reopen only the affected decision for a new source, a version/use outside its approval scope, or a changed deliverable. An answer about intent, an unsubmitted UI default, or elapsed time is not approval. Routine analysis and formatting do not create another gate.
