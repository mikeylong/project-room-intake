# Bounded comparison records

Use this optional workflow when evaluating the skill's benefit. It does not run an agent, discover sources, obtain approval, or judge whether a source is relevant. Choose authorized material and define importance, relevance, source versions, task, and measurement rules before either arm starts. Compare ordinary bounded intake with the skill under the same task and source boundary.

`scripts/score_trial.py` freezes those criteria and scores two recorded observations. It uses only the Python standard library and reads only the explicitly supplied JSON files; source IDs are data, never paths to open. The helper does not change normal intake or its approval rules.

## Freeze the criteria

Prepare a protocol with exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `project-room-intake.trial.v1` |
| `trial_id` | Nonempty identity for this comparison |
| `task` | The common task, including source/use boundaries and desired output |
| `corpus_revision` | Identity of the fixed source set, such as a repository commit plus snapshot description |
| `arms` | Exactly two distinct IDs, with the ordinary baseline first and revised workflow second |
| `sources` | Nonempty list of `{source_id, sha256, important, relevant}`; IDs are unique, SHA-256 values are 64 lowercase hexadecimal characters, and flags are booleans |
| `confounders` | List of known limitations, including task difficulty, familiarity, model/client differences, and order effects where applicable |
| `measurement_notes` | Definitions and collection method for source nomination/use, corrections, clarification turns, and elapsed/human effort |

An important source must also be relevant. Importance and relevance are task-specific judgments, not attributes inferred from filenames or hashes. Include the complete authorized source set in the protocol so undeclared source IDs cannot silently enter scoring. The [synthetic fixture](../tests/fixtures/trials/protocol.json) illustrates the format; its hash values are illustrative, not evidence of actual source contents.

```sh
python3 scripts/score_trial.py freeze \
  --protocol /path/to/trial/protocol.json \
  --output /path/to/trial/frozen.json
```

The new file has `schema_version: project-room-intake.trial-freeze.v1`, `protocol_sha256`, and `protocol`. The digest covers UTF-8 JSON with sorted object keys, compact separators, `ensure_ascii=False`, and no nonfinite numbers. List order remains significant. Preserve this file and digest before dispatching either arm, ideally with an independent dated record or commit. Do not revise the criteria after seeing results; a revised protocol is a new comparison.

The digest binds observations to criteria and detects accidental edits. It does not prove when the file was created or prevent someone from rewriting both protocol and digest. Record chronology and source identities separately. Keep the importance/relevance labels away from evaluating agents when a blinded source-selection exercise is intended.

## Record each arm

Supply one observation per arm with exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `project-room-intake.trial-observation.v1` |
| `trial_id`, `protocol_sha256` | The frozen trial identity and unchanged digest |
| `arm_id` | One of the two declared arm IDs |
| `candidate_source_ids` | Unique declared IDs nominated for review/use; distinguish these from files merely enumerated |
| `used_sources` | Unique `{source_id, sha256}` entries used as evidence in the result; every used source must be a candidate, and `sha256` is the observed lowercase digest or `null` if unverified |
| `user_corrections` | Nonnegative integer count, or `null` when not observed |
| `clarification_turns` | Nonnegative integer count, or `null` when not observed |
| `elapsed_seconds` | Nonnegative finite wall-clock duration, or `null`; describe start/end boundaries and queue/service effects in measurement notes |
| `human_effort_minutes` | Nonnegative finite active human effort, or `null`; agent duration is not a substitute |
| `notes` | List of observation-specific limitations or recording details |

Use `null` rather than zero for unmeasured corrections or effort. A synthetic run without human participation does not establish zero user effort or corrections. Record source use and revisions from inspectable artifacts or logs; a source merely being present in the corpus does not establish that an agent used it. Add new confounders discovered during execution to observation notes without rewriting the frozen protocol.

For example, an observation can nominate `important-a` and record it as used with `sha256: null`; this identifies the source but does not establish version correctness. Do not copy the expected protocol hash into an observation unless the observation actually supports that identity.

## Score and interpret

```sh
python3 scripts/score_trial.py score \
  --protocol /path/to/trial/frozen.json \
  --observation /path/to/trial/ordinary.json \
  --observation /path/to/trial/skill.json \
  --output /path/to/trial/comparison.json
```

Argument order does not change comparison order: the frozen `arms` list controls it. The result uses `project-room-intake.trial-result.v1`, retains the protocol digest, corpus identity, confounders, and measurement notes, and reports ordered arm metrics and second-minus-first deltas.

| Metric | Calculation |
| --- | --- |
| `missed_important_sources` | Important protocol IDs absent from `used_sources` |
| `irrelevant_candidates` | Nominated candidate IDs not marked relevant |
| `version_mistakes` | Used IDs with a nonnull observed digest different from the frozen expected digest |
| `unverified_versions` | Used IDs with a null observed digest; reported separately, never credited as matching content |
| Corrections, clarification turns, elapsed seconds, human effort | Recorded scalar values, preserving unknown values as null |

The four source metrics include both `count` and sorted `source_ids`. Source identity and version correctness are separate: using an important source at an incorrect or unverified version reduces the missed-source count but appears in the appropriate version metric. Interpret these measures together.

Each scalar delta is null if either arm's measurement is unknown. Count deltas compare the recorded sets' sizes. A negative or positive difference is descriptive; the tool does not declare a winner, effect size, causal improvement, or significance. An all-zero result can reflect an easy task, generous source boundaries, or measurement limits.

Both commands refuse to overwrite an existing output. Exit `0` means the new result was written; `1` means invalid/incompatible records or an input/output error; `2` means invalid command-line arguments. Unknown fields, duplicate IDs or JSON keys, missing/duplicate arms, observations from another frozen trial, boolean counts, negative values, and nonfinite numbers are rejected. Validation completes before the output file is created. Preserve originals when correcting an observation and write a newly named score result with an explanation of the correction.

Review underlying artifacts as well as scores. The helper cannot establish that records are honest, that a citation supports a claim, that a user trusted the result, or that one bounded comparison generalizes. Keep public benefit claims qualified until suitable evidence supports them. See [validation evidence and environment scope](validation.md) for the repository's tested paths and recorded trial limitations.
