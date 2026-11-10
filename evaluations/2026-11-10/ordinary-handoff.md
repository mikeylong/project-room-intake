Engineering handoff for snapshot `43259d6cc706653a857966b346f3925da549debc`.

Nominated evidence: `README.md`, `references/intake-contract.md`, `scripts/{scan_sources,review_sources,intake_store}.py`, and `tests/{test_scanner,test_review_state,test_contract}.py`. These cover operation, implementation, and regression expectations.

Run on POSIX with Python 3.10+. From the checkout, this illustrative mixed-file intake names each approved file and a separate local room:

```sh
intake=(python3 scripts/scan_sources.py
  --room /path/to/room
  --file /path/to/sources/brief.md
  --file /path/to/sources/metrics.csv
  --file /path/to/sources/design.pdf)
"${intake[@]}" --metadata-only
# Only when hashing these files is authorized:
"${intake[@]}" --hash-content --no-snippets
```

Repeated `--file` inputs inspect only named entries, opening ancestor directories without enumerating or inspecting siblings. A directory passed as `--file` causes a coverage error; it never becomes recursive authorization. Entry symlinks are inventoried without target reads; symlink ancestors and `..` paths are rejected. Exact files bypass hidden/default exclusions, but user exclusions still win. Recursive `--root` inputs instead prune hidden/default descendants; includes cannot restore them. Includes match root-relative paths/basenames for roots, absolute paths/basenames for files. User exclusions also match ancestors, stopping at the outermost containing declared root; standalone exact files use their full ancestor chain. Overlap produces one candidate while retaining successful routes in `discovered_via`. See [scanner](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/scan_sources.py#L37) and [boundary tests](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/tests/test_scanner.py#L435).

Metadata-only opens no source content. Hashing and snippets are independent opt-ins; `--metadata-only` conflicts with either, while `--no-snippets` leaves hashing enabled. Hashing defaults to a 50 MiB limit (`--hash-max-mb` adjusts it). Previews read at most 8,192 bytes from files at most 5 MiB, producing up to 600 characters; NUL-containing previews are skipped. This is byte/text previewing, not PDF or spreadsheet parsing. Inspect per-file statuses: a skipped or failed hash leaves no verified revision. Size/mtime and same-size hints cannot establish content equality. [Implementation](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/scan_sources.py#L167)

Read `candidate_sources.json` with `scan_summary.md`; JSON is authoritative and CSV is a generated export. For a currently observed candidate, copy its ID and the exact approved version’s 64-character lowercase hash into this template:

```sh
python3 scripts/review_sources.py \
  --room /path/to/room \
  --source-id src_SOURCE_ID --decision approved --scope snapshot \
  --expected-sha256 REVIEWED_SHA256 --actor "Mike" \
  --notes "Approved this version for the handoff"
```

Every snapshot decision, including `excluded` or `deferred`, requires the expected hash. The CLI compares it with the published revision under lock; it does not rehash the live file. Never substitute a newer hash for the approved one. `--scope source` omits the hash and requires explicit authorization across future observations. For authorized current use without a verified revision, preserve that permission and “version unverified” in the provenance ledger; do not invent snapshot approval. Source approval and drafting authorization remain distinct. [CLI](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/review_sources.py#L11), [contract](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/references/intake-contract.md#L83)

Rescans preserve decision history, notes, and classification. Matching snapshot revisions retain applicability; changed/unknown revisions become `needs_review`. An absent candidate becomes `missing` only after complete discovery of its prior scope; otherwise it is `unobserved`. [Store](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/intake_store.py#L425)

Scanner exits: `0` complete; `2` argument error; `3` partial with useful artifacts; `4` failed with evidence; `5` output/compatibility failure. Empty or deliberately excluded inputs can be complete. Inspect per-input coverage errors separately from optional enrichment errors. Never call partial coverage exhaustive or output failure a valid new result. [Contract](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/references/intake-contract.md#L59)

Candidate generations publish atomically. Review history commits first: if view refresh fails, inspect `review_decisions.json` before retrying; a successful rescan repairs the view. Review exits are `0` success, `1` room/write/revision failure, `2` invalid arguments. Preserve legacy backups and resolve compatibility refusals without overwriting reviewed data. [Recovery tests](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/tests/test_review_state.py#L184), [validation tests](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/tests/test_contract.py#L53)

This handoff inspected code and tests; it did not execute them. Implemented safeguards do not establish improved recall, writing quality, or trust. [README](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/README.md#L175)
