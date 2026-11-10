This handoff describes the supplied snapshot `43259d6cc706653a857966b346f3925da549debc`. Source links resolve to that local corpus. Commands below are illustrative; substitute approved inputs and a separate local room.

Start with metadata discovery. Repeat `--file` for individually approved notes, tables, or binary exports; use `--root` only when the directory itself is approved. Both input types can be combined. For example:

```sh
tool=/path/to/project-room-intake
room=/path/to/room
intake_args=(--room "$room"
  --file /path/to/sources/notes.md
  --file /path/to/sources/costs.csv
  --file /path/to/sources/export.pdf)
python3 "$tool/scripts/scan_sources.py" "${intake_args[@]}" --metadata-only
```

An exact-file route opens ancestor directories and inspects only the named entry, without listing or inspecting siblings. Its displayed parent `root` grants no directory scope. A directory passed through `--file` produces a coverage error. Symlink entries are inventoried without following targets; symlink ancestors and `..` paths are rejected. Keep inputs and room disjoint. Repeated/overlapping inputs produce one candidate while preserving each successful `discovered_via` route. [Contract:24–26,57–71](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/references/intake-contract.md#L24)

Recursive discovery applies hidden/default exclusions before includes. An exact named file bypasses those defaults, but includes still narrow it and user exclusions always win, including ancestor exclusions. Naming an excluded descendant cannot restore it. Metadata mode opens no source contents, including empty or binary files. [Scanner:85–120,167–178,290–345](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/scan_sources.py#L85)

Hashing and previews require separate content-access authorization:

```sh
python3 "$tool/scripts/scan_sources.py" "${intake_args[@]}" --hash-content --no-snippets
```

`--metadata-only` conflicts with hashing or snippets; `--no-snippets` only disables previews. By default, files over 50 MiB are not hashed; previews read at most 8,192 bytes from files up to 5 MiB and omit content containing NUL bytes. This is inventory/enrichment, not PDF or spreadsheet parsing. Check per-source statuses; size/mtime cannot establish content equality. [Scanner:37–67,180–237](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/scan_sources.py#L37)

Read `candidate_sources.json` and `scan_summary.md` together. JSON is authoritative; CSV is a generated review view. Record an authorized decision using the observed `source_id` and the approved version’s SHA-256:

```sh
python3 "$tool/scripts/review_sources.py" --room "$room" \
  --source-id "$source_id" --decision approved --scope snapshot \
  --expected-sha256 "$approved_sha256" --actor Mike \
  --notes "Current source use preauthorized; hash observed by agent"
```

Set those variables from the applicable authorized observation. For a previously reviewed snapshot, retain its hash even after a rescan; never replace it with the changed version’s hash. Snapshot decisions compare expected/current revisions under the room lock. `source` scope covers future versions only when explicitly authorized and rejects `--expected-sha256`. Classifications do not confer approval. [Review CLI:11–34](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/review_sources.py#L11), [contract:87–101](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/references/intake-contract.md#L87)

Refresh preserves decision history and notes. Matching hashes reuse snapshot decisions; changed or unknown revisions become `needs_review`. Absent candidates become `missing` only after complete discovery of their previous scope; partial or changed scope leaves them `unobserved`. [Store:445–459,499–539](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/scripts/intake_store.py#L445)

Handle scanner exits explicitly: `0` complete; `3` partial with useful published evidence; `4` failed discovery with published evidence; `5` compatibility/publication failure; `2` invalid arguments. Intentional exclusions can still yield complete coverage. Optional hash/preview errors are separate enrichment failures. Inspect per-input errors; never call partial coverage exhaustive. [Contract:73–83](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/references/intake-contract.md#L73)

Candidate JSON/CSV/summary publish as one generation. Review history saves first, then views refresh: on review exit `1`, inspect `review_decisions.json` before retrying. A saved decision can coexist with an old readable view; a successful rescan repairs it. Review exit `2` means invalid arguments. An output failure is not a new scan result. [Contract:101–111](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/references/intake-contract.md#L101)

Inspected tests assert sibling isolation and failure recovery; they were not run for this handoff. Implemented safeguards do not establish improved recall, writing quality, or trust. [Scanner tests:435–466](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/tests/test_scanner.py#L435), [review tests:184–206](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/tests/test_review_state.py#L184), [README:181](https://github.com/mikeylong/project-room-intake/blob/43259d6cc706653a857966b346f3925da549debc/README.md#L181)
