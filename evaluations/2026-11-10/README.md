# Repository handoff comparison

One bounded comparison found no difference on its predefined source-selection or version metrics. The skill arm produced the additional room artifacts and took longer in observed wall time. This is a descriptive result for one repository task, not evidence for or against the skill's benefit across real projects.

## Task and chronology

Both fresh Codex agents received the same engineering-handoff request and read-only access to all 14 tracked files at commit `43259d6cc706653a857966b346f3925da549debc`. Reading, hashing, and drafting were authorized in advance. The ordinary arm used bounded file listing and reading; the skill arm received an explicit `SKILL.md` path and used its scanner and review CLI. Both agents inherited the same model without overrides; sampling was not controlled. Neither was given the important/relevant source labels or the other arm's output.

The four important sources were the scanner, review CLI, persistence implementation, and intake contract. Supporting sources were relevant except `agents/openai.yaml`, which describes client UI metadata outside this CLI handoff. These are evaluator judgments for this task, not a user-validated gold standard.

[Criteria](frozen.json) were frozen at `2026-11-10T21:31:03.928664+00:00`, before either dispatch. Protocol SHA-256: `71192c2570f738455856319be4d84641fd722866a4657fe0ef41e64f92162d3d`. [Execution metadata](execution.json) records dispatch/completion boundaries, source and artifact checks, and the skill/contract identities. The digest binds records to criteria; the preserved chronology supports the freeze order but is not independent timestamp attestation.

## Observations

| Measure | Ordinary intake | Skill intake |
| --- | ---: | ---: |
| Important sources missed among used evidence | 0 of 4 | 0 of 4 |
| Irrelevant nominated candidates | 0 | 0 |
| Reported version mistakes | 0 | 0 |
| Unverified used-source hashes | 0 | 0 |
| Clarification requests | 0 | 0 |
| Sources nominated / used | 8 / 8 | 9 / 7 |
| Handoff words, whitespace count | 550 | 531 |
| Dispatch-to-completion wall time | 143.475 seconds | 258.134 seconds |
| User corrections | Unmeasured | Unmeasured |
| Active human effort | Unmeasured | Unmeasured |

The observed wall-time difference was 114.659 seconds. This includes queueing, tool latency, parallel contention, and the skill's additional artifact work; it is not measured human cost or a causal timing estimate. Null measurements were not converted to zero.

All used-source hashes matched the snapshot; cited source IDs matched the usage records and their link start lines existed. The corpus was unchanged. Both handoffs remained below the word limit and qualified outcome claims. Claim quality and user trust were not independently scored. The skill arm additionally published complete coverage, recorded nine snapshot decisions under existing authorization, prepared all six room artifacts, and drafted without redundant approval requests. This exercises explicit agent invocation, not installed-client UI discovery or implicit selection.

## Records and reproduction

- [Ordinary observation](ordinary-observation.json) and [skill observation](skill-observation.json) retain source nominations, reported use, measurements, and limitations.
- [Comparison](comparison.json) contains the scorer's unrounded values and second-minus-first deltas.
- [Ordinary handoff](ordinary-handoff.md) and [skill handoff](skill-handoff.md) are portable copies: local source links point to the exact repository commit and example paths use placeholders. `execution.json` distinguishes original-output hashes from copy hashes. No content was improved after scoring.

From the repository root, reproduce the calculation with a new output path:

```sh
python3 scripts/score_trial.py score \
  --protocol evaluations/2026-11-10/frozen.json \
  --observation evaluations/2026-11-10/ordinary-observation.json \
  --observation evaluations/2026-11-10/skill-observation.json \
  --output /path/to/new-comparison.json
```

Recomputing the score reproduces arithmetic over records; it does not rerun agents or independently establish their observations. The source snapshot can be reconstructed from the named commit and verified against the per-source hashes in `frozen.json`. Original prompts, unedited outputs, room generations, logs, and source copies are retained in the implementation task's local evidence archive; they are not needed by the scorer.

## Limits and resulting decision

The task uses the skill's own small repository, includes only one irrelevant source, and contains no stale-version variants. Both agents had complete authorization and a fully stated task. Source availability was generous, the baseline could read skill text as project evidence, and no human participant supplied corrections. These conditions limit the sensitivity and generality of every result. The full frozen confounders remain in the records.

Keep public benefits framed as intended goals. This trial supports the explicit invocation path and the ability to collect a comparable record, but demonstrates no source-selection advantage on this task. It does not justify changing the approval gates or claiming improved recall, writing quality, trust, or human effort. A broader outcome claim would need authorized representative tasks, a user-reviewed important-source set, and actual human measurements; no such follow-up is implied by ordinary intake.
