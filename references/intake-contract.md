# Project Room Intake Contract

This reference defines stable artifacts for project/data-room intake. The room is a reviewable evidence workspace, not a hidden context dump.

## Candidate Discovery Artifacts

`scripts/scan_sources.py` publishes these artifacts for approved local directory roots and exact-file inputs:

- `candidate_sources.csv`: Spreadsheet-friendly candidate source table.
- `candidate_sources.json`: Authoritative machine-readable observations in `project-room-intake.candidates.v2`, including input scope, content-access mode, discovery coverage, errors, candidates, exclusions, and joined review status.
- `scan_summary.md`: Human summary of coverage, input outcomes, errors, candidate count, exclusions, duplicate hints, and top relevance matches.
- `review_decisions.json`: Durable decisions and history in `project-room-intake.reviews.v1`, separate from generated source observations.

Each CSV row includes `scan_id`, `generation_id`, and `coverage_status`; an empty CSV still requires the JSON/summary for coverage.

The machine contracts are [candidates.v2.schema.json](schemas/candidates.v2.schema.json) and [reviews.v1.schema.json](schemas/reviews.v1.schema.json). JSON is the authoritative structured record; CSV is a review/export view, not the place to store approval edits. The scanner does not import connector manifests or CSV edits.

[Representative fixtures](../tests/fixtures/contracts/candidates.json) cover local files, local symlinks, unretrieved and retrieved connector metadata, and partial discovery. `scripts/intake_store.py` validates the operational contract before publishing; malformed nested metadata must not replace a reviewed room. The fixtures demonstrate representation and preservation, not a connector retrieval capability.

Candidate observations retain these source fields:

- `source_id`: Stable ID derived from source type and local path or external URI.
- `source_type`: `local_file`, `local_symlink`, or manifest-defined connector/blob type.
- `root`: Directory root associated with the candidate's first successful observation, or manifest source. For an exact-file observation, its parent is a display base only, not an authorization to scan that directory.
- `path`: Absolute local path, or stable external URI/key for manifest rows.
- `relative_path`: Path relative to the first observation's display base when local; an exact-file observation uses its basename.
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
| `discovered_via` | Optional list of successful input routes with `kind` (`root` or `file`) and `path` (the absolute approved input); retain repeated input occurrences |
| `provider`, `version`, `retrieval_status` | Provider/retrieval metadata; unknown values must not be fabricated |
| `observation_status` | `observed`, `missing`, or `unobserved` |
| `last_observed_at`, `observation_scope` | Last successful observation and the scan scope associated with it |
| `review` | Joined decision status/reason and decision provenance; not a replacement for the review record |
| `extensions` | Additional metadata kept separate from the shared fields |

Revision evidence and the joined review state keep observations separate from authorization. `review.status` can be `unreviewed`, `approved`, `excluded`, `deferred`, `needs_review`, or `unavailable`; `review.reason` explains its applicability. A classification describes relevance or use; it does not approve a source. A prior decision remains in history even when its scope does not apply to the current observation.

Top-level `roots` records directory inputs, and optional `files` records exact-file inputs. Candidate `discovered_via` preserves the successful routes when inputs overlap or repeat; one source still has one candidate record. `root` and `relative_path` retain the first observation's display base rather than replacing that complete provenance. Each coverage input outcome adds `kind` and `path` so file and directory routes are distinguishable.

Older v2 artifacts without `files` remain valid; an absent or empty file list means the same scope. Scope comparison treats roots, files, and include/exclude patterns as sets, while provenance and per-input outcomes preserve repeated occurrences. Retained missing/unobserved candidates keep their last observed scope and provenance. Do not invent `discovered_via` for older v2 or v1 artifacts.

## Content Access and Coverage

The local scanner is metadata-only by default. It may inspect filesystem metadata but must not open source contents to hash, preview, or parse them. `--metadata-only` makes the mode explicit and conflicts with `--hash-content` and `--snippets`. Those two flags independently request content reads and require authorization for their use. `--no-snippets` only disables previews; it does not disable an explicitly requested hash.

Metadata-only relevance uses names and paths. Local size and modification time cannot establish content equality or a known snapshot revision. Exact provider content identities, when available as metadata, can support duplicate hints if their semantics and provenance are recorded; an arbitrary etag or object ID is not automatically a content checksum.

Supply at least one `--root` or `--file`; both flags are repeatable and can be combined. Roots must be directories without symlink components. An exact-file input opens only its ancestor directories and inspects the named entry, without enumerating or inspecting siblings. A directory selected through `--file` is a coverage error, never a recursive root. Source entry symlinks, including exact-file symlinks, are inventoried without reading their targets; symlink ancestors are rejected. Name inputs and the room directly; paths containing `..` are rejected. The room and source roots must not overlap, and exact-file inputs must remain outside the room.

For a recursive root, hidden/default exclusions prune descendants before include filtering. Explicitly naming a hidden root permits entering that root, not bypassing its descendant filters. An exact-file input bypasses hidden/default exclusions for that named path, including excluded ancestor directory names. Includes narrow both routes: root-relative paths/basenames for recursive entries, absolute paths/basenames for exact files.

User exclusions take precedence for roots, recursive entries, and exact files. They match absolute paths/basenames, ancestor paths/names, and paths relative to any containing declared root. Ancestor matching stops at the outermost containing root, including that root's name; ancestors above an approved root are not candidates. An exact file outside all declared roots uses its full ancestor chain. These checks use path strings before filesystem access. A user-excluded root is intentionally excluded without opening it, and a user-excluded subtree cannot be restored by naming one of its files through `--file`. Repeating a file input or discovering it through a root does not authorize any sibling access through the file route.

Coverage distinguishes:

| State | Meaning |
| --- | --- |
| `complete` | Every approved input was inspected or intentionally excluded under the declared filtering policy, without coverage errors; an accessible empty root or all-excluded input set can be complete |
| `partial` | Some input scope was inspected or intentionally excluded, while discovery errors prevented complete coverage |
| `failed` | No input completed and errors prevented all inspection |

Per-input outcomes and structured errors explain coverage. Deliberate exclusions do not themselves make a scan partial. Optional hash or preview errors are enrichment failures and do not themselves mean directory discovery failed. Do not describe a partial result as exhaustive or silently mark an unobserved source missing.

Exit codes: `0` means complete discovery with published artifacts; `3` means partial discovery with evidence published; `4` means failed discovery with evidence published; `5` means room compatibility or output publication failed; `2` means invalid command-line arguments. A nonzero coverage result can still contain useful candidates. An output failure is not a valid new scan result.

## Durable Review Decisions

Use `scripts/review_sources.py` to record explicit user decisions. It requires `--room`, `--source-id`, `--decision approved|excluded|deferred`, `--scope snapshot|source`, and `--actor`; `--notes` and `--classification` are optional free text. Snapshot scope additionally requires `--expected-sha256 HASH`; that flag is rejected with source scope. It records the decision; it does not obtain authorization from the user or grant itself permission to read sources. The source must be present as currently observed in a published v2 room.

Each decision records `decision_id`, `source_id`, `decision`, `scope`, `revision`, `actor`, `notes`, `classification`, and `recorded_at`. Decisions append to history. Omitted notes and classification retain the earlier values for that source. The CLI joins the relevant decision to generated observations without replacing the original approval record.

- **Snapshot scope** binds a decision to exact verified content within the user's authorization. Every snapshot-scoped decision, including exclusion or deferral, requires an expected SHA-256 and a matching verified current revision. The API receives `expected_revision` as `{ "kind": "sha256", "value": "<64 lowercase hexadecimal characters>" }`; the CLI receives its value through `--expected-sha256`. The expected/current comparison occurs under the room lock before saving; missing or mismatched expectations fail. When the user approves a previously presented snapshot or manifest, the expectation must remain that approved version's hash, never a rescan's replacement. When the user preauthorizes a current named source and its retrieval/hashing, use the exact content hash observed during that authorized operation and record its agent-observed provenance; do not claim the user reviewed a hash or manifest. The same mismatch protection applies if the room changes before that decision is saved. A size/mtime match is insufficient, and metadata-only discovery must not hash content to satisfy this requirement. Changed or unknown revisions do not silently inherit snapshot approval.
- **Source scope** explicitly applies across current and future observations of that source. Use it only when the user authorizes that broader scope; current-file preauthorization does not imply it. Do not substitute it merely because a snapshot cannot be verified.
- **Unobserved versus missing:** a complete scan of the same source scope can establish that a prior source is missing. Partial coverage or a changed scan scope cannot. Preserve earlier decisions and notes in either case.

Record permission now; verify the version separately. When a metadata-only room has no verified hash, immediately preserve the exact current-use instruction in `provenance_ledger.md`, with the source, use limits, actor, time (unknowns explicit), and **version unverified**. Reuse that contemporaneous permission to read/use the current named source within its limits while the version-bound CLI decision remains unresolved. Do not fabricate a snapshot approval or substitute future source-scope approval.

Obtain version evidence only through already-authorized retrieval/hashing. If recording a snapshot requires verification that is not authorized, ask only for the missing verification or scope decision, not for approval of the source again. Historical snapshot approval is different: it does not authorize changed or unverified current content. A new current-use instruction must be recorded as such; it does not erase an old snapshot mismatch, authorize future versions, or make an unknown version verified.

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
