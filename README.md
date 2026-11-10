# Project Room Intake

Project Room Intake helps get the right source material in place before writing starts.

It is useful when the work depends on more than one file, folder, export, transcript, or shared source. Instead of jumping straight into a memo, PRD, audit, report, or research brief, the skill creates a short intake step: what sources exist, which ones look useful, what may be stale or duplicated, and what still seems missing.

The intended benefits are better source coverage, fewer accidental assumptions, and a clear moment for human review before drafting begins. Current tests establish specific discovery and review behaviors; they do not establish better real-world recall, writing quality, or user trust.

## When To Use It

Use this skill when the project has enough source material that it would be easy to miss something important.

Good examples:

- A board memo built from deal notes, financial files, customer docs, and meeting transcripts.
- A PRD based on research notes, support tickets, product docs, and prior plans.
- A content strategy project with interviews, analytics exports, examples, and old drafts.
- An audit where old files, duplicate versions, and missing context could change the answer.
- Any serious writing task where the sources matter as much as the prose.

This skill is probably too much for a simple rewrite, a one-file summary, or a small coding task.

## What It Helps Prevent

Important work can go sideways before the first paragraph is written.

Common failure modes:

- The draft uses the loudest source, not the best source.
- Old files get treated as current.
- Duplicate versions hide the real source of truth.
- Missing context is discovered too late.
- Private or sensitive material gets pulled into the work without an explicit review step.
- The final answer sounds confident, but the source set was incomplete.

Project Room Intake provides an explicit source-review step. Whether that step improves the result enough to justify its effort needs a task-specific comparison.

## What It Does

The skill asks for clear boundaries first: what topic the room is for, where to look, what dates matter, and what counts as an authoritative source.

Then it creates a candidate source list from the approved locations. It does not roam through broad folders or silently import everything it can find.

After discovery, it gives the user a reviewable set of artifacts:

- A list of candidate sources.
- A summary of what was found and what was excluded.
- Duplicate and stale-file hints.
- Notes about missing context.
- A clear next step: approve sources, add more locations, or stop.

There are two review decisions. First, approve the candidate sources and their intended use. The skill then prepares the room. Second, review the completed room and authorize the deliverable. Explicit authorization is reused within its recorded scope; retrieving an already approved source or formatting the room does not require another confirmation.

## What You Get

After discovery, you get a compact view of the possible sources before any deliverable is written.

After approval, the skill can create a project room with:

- `source_inventory.md`
- `conflict_log.md`
- `missing_context.md`
- `duplicate_report.md`
- `provenance_ledger.md`
- `PROJECT_BRIEF.md`

Those files make it easier to see what the work is based on, what may be in tension, and what should still be checked.

The local scanner also maintains a durable `review_decisions.json`. It keeps source decisions and notes separate from generated observations so rescanning does not replace them. The scanner prepares candidate artifacts; it does not automatically write the six room documents or the final deliverable.

## Invocation Examples

These examples describe the intended workflow. The [validation record](references/validation.md) distinguishes tested invocation paths from unverified client behavior.

```text
Set up a project room for a board memo using the files in [project folder] and the attached source manifest. Do not draft yet.
```

```text
Use project-room-intake for a PRD on onboarding improvements. Look in [research folder] and [planning folder]. I want to review the source list before you write anything.
```

```text
Create a project room for an audit of [topic]. Use only the approved folders I provide, flag duplicates or stale files, and pause before writing the final report.
```

The important part is giving the skill a bounded place to look. It is intentionally cautious about broad searches.

## Local Scanner

The scanner uses Python 3.10+ syntax and POSIX filesystem APIs. The current verified runtime is macOS; [tested interpreter versions and support limits](references/validation.md) are recorded separately. Linux and Python 3.10 are unverified; Windows is not supported. Run it from this repository and keep the room separate from the approved source roots:

```sh
python3 scripts/scan_sources.py \
  --room /path/to/project-room \
  --root /path/to/approved-source-root \
  --query "board memo"
```

This is **metadata-only by default**. It inventories paths and file metadata without opening source contents for hashes or previews. Add `--metadata-only` to make that choice explicit. Symlink entries are inventoried without following their targets; a root with a symlink component is refused. Source roots and the room must not overlap. Name inputs directly; paths containing `..` are rejected so normalization cannot hide a symlink boundary.

Hashing and previews read source contents and are separate opt-ins. Use them only when those operations are authorized for the selected inputs:

```sh
python3 scripts/scan_sources.py \
  --room /path/to/project-room \
  --root /path/to/approved-source-root \
  --hash-content --snippets
```

`--hash-content` and `--snippets` can be used independently. `--metadata-only` conflicts with either opt-in. `--no-snippets` disables previews only; it does not cancel `--hash-content`. This changes the earlier default, which read files for hashes and snippets. Metadata-only relevance uses names and paths; local content duplicates cannot be established from size and timestamps alone. Content-addressed provider identities can still support documented metadata hints in a separate connector manifest.

Use `--file` when approval covers an exact file rather than a directory:

```sh
python3 scripts/scan_sources.py \
  --room /path/to/project-room \
  --file /path/to/approved/notes.md \
  --file /path/to/approved/.hidden-evidence.json \
  --metadata-only
```

Repeat or combine `--root` and `--file`; at least one input is required. Exact-file intake inspects only the named entry, without enumerating or inspecting siblings. A directory selected through `--file` is an input error and is not recursively scanned. A symlink selected through `--file` is inventoried as a link without reading its target; a symlink in an ancestor path is rejected. File approval never grants access to the parent directory's other contents.

Exclusion rules depend on the input route:

| Input | Hidden/default exclusions | User filters |
| --- | --- | --- |
| Recursive `--root` | Apply to descendants; an include glob cannot restore excluded entries. An explicitly named hidden root itself may be entered. | Includes match root-relative paths or basenames. User exclusions apply to the input and its descendants. |
| Exact `--file` | Do not hide an explicitly named file, including a file beneath a normally excluded directory. | Includes match its absolute path or basename. User exclusions still win. |

User exclusions match absolute paths/basenames and ancestor paths/names. Within declared roots, ancestor matching stops at the outermost containing root; ancestors above it do not affect the result. Patterns also match paths relative to any containing root. An exact file outside every declared root uses its full ancestor chain. A user-excluded root is skipped before it is opened.

An explicit file never bypasses a user exclusion. For example, excluding `private` or `private/*` beneath a declared root still excludes `private/note.md` when it is also named with `--file`, while `/private` above that root does not exclude the whole scan. Naming a file inside `.cache` bypasses the default exclusion, but an explicit `--exclude-glob .cache` still excludes it within the matching boundary above.

The scanner keeps one candidate per source and records all successful input routes in `discovered_via`, including repeated inputs and root/file overlap. Every supplied input has its own coverage outcome. The scanner does not import connector manifests.

Read `scan_summary.md` and `candidate_sources.json` together. Discovery is `complete` when every input was inspected or intentionally excluded without a coverage error; an accessible empty root and an all-excluded input set can be complete. It is `partial` when some input scope was inspected or intentionally excluded while discovery errors prevented complete coverage. It is `failed` when no input completed and errors prevented all inspection. Per-input outcomes identify the input kind and path. Optional hashing/preview failures are recorded separately from coverage. Exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | Complete discovery and published artifacts |
| `2` | Invalid command-line arguments |
| `3` | Partial discovery; useful results published |
| `4` | Failed discovery; failure evidence published |
| `5` | Output or room-compatibility error; inspect the error message |

Do not treat a partial inventory as proof that all approved inputs were inspected.

## Review Decisions and Rescans

After the user approves a known hash-identified version, record that decision with the source ID from the candidate manifest:

```sh
python3 scripts/review_sources.py \
  --room /path/to/project-room \
  --source-id src_SOURCE_ID \
  --decision approved --scope snapshot \
  --expected-sha256 REVIEWED_SHA256 \
  --actor "Mike" --notes "Approved this reviewed version"
```

Replace `REVIEWED_SHA256` with the 64-character lowercase SHA-256 from the exact manifest/version the user reviewed. Snapshot scope requires this expected hash and a verified current revision, obtained only through authorized hashing. The command compares them under the room lock before saving a decision. If a rescan replaces reviewed version A with version B, an approval for A fails; do not substitute B's hash without reviewing B.

Metadata-only size and timestamps are not proof of an unchanged version. Use `--scope source` only when the user explicitly approves the source across current and future observations; omit `--expected-sha256` for that broader scope. Decisions can also be `excluded` or `deferred`; classifications and notes remain separate from approval.

Rescanning retains decision history. A changed or unknown revision does not silently inherit snapshot approval, and a source unseen during incomplete or differently scoped discovery is not declared missing. Existing authorization still applies when its recorded scope covers the observation.

Candidate artifacts are published as an atomic generation. Supported v1 rooms are backed up before conversion; unknown fields/extensions or inconsistent candidate CSV/JSON cause an actionable refusal instead of being discarded. This check cannot detect coordinated edits to both files, so preserve and inspect the backup. Legacy summary notes are preserved in that backup.

The review CLI commits the authoritative decision before refreshing candidate views. If that refresh fails, the decision still persists and the previous candidate generation remains readable. Inspect `review_decisions.json` before retrying; a subsequent successful scan refreshes the view. The [intake contract](references/intake-contract.md) defines the schemas, version checks, and recovery behavior.

## Tests

Run the focused scanner and room-persistence tests without writing Python bytecode:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

These tests check bounded discovery and preservation behavior. They do not establish improved source recall, writing quality, or user trust in real projects.

Run the repeatable CLI smoke check against this checkout with a new evidence directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/smoke_test.py run \
  --output /path/to/local/smoke-runs/run-001
```

The harness creates disposable synthetic sources, checks discovery and review lifecycles, preserves evidence, and removes its owned fixtures. Add `--skill-dir /path/to/installed/project-room-intake` to check an installed copy. Existing output directories are refused. The [smoke-test guide](references/smoke-test.md) also documents the staged agent path for room preparation and drafting, failure cleanup, evidence, and limits. The one-command check does not run an agent or establish human approval quality.

[Contract examples](tests/fixtures/contracts/candidates.json) cover local files, symlinks, unretrieved/retrieved connector metadata, and partial discovery. [Manual approval scenarios](tests/approval_scenarios.md) provide reusable requests and decision criteria for evaluating the skill's approval behavior separately from scanner correctness.

## Bounded Evaluation

For an explicit evaluation request, use the [trial protocol](references/trial-contract.md) to freeze the task, authorized source identities, important/relevant source sets, comparison arms, measurement rules, and confounders before either run. `scripts/score_trial.py` validates recorded observations and computes descriptive counts and differences; it does not retrieve sources, run agents, infer missing measurements, or declare a winner.

The [validation record](references/validation.md) separates runtime checks, agent invocation evidence, and the bounded comparison. Unknown human effort and user corrections remain unknown. A small repository trial cannot establish effectiveness across real projects or client installations.

## Why The Review Step Matters

Source selection deserves review alongside the draft itself.

Project Room Intake gives the human a chance to catch a source problem early: wrong folder, missing export, stale deck, duplicated notes, or a source that should be excluded. The effect on writing quality and trust remains to be measured.
