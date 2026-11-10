# Validation and supported use

This record separates executed behavior from intended benefits. The supported, verified path is the local command-line tooling on the macOS combinations below, plus explicit skill-file invocation in a Codex agent session. Other combinations require their own validation before relying on them.

## Runtime scope

Validated on November 10, 2026, on macOS 27.2, arm64:

| Interpreter | Executed check | Result |
| --- | --- | --- |
| Python 3.11.13 | Full unittest suite, including scanner, review CLI, trial CLI, smoke lifecycle, and access-guard cases | 107 passed |
| Python 3.12.14 | Same suite | 107 passed |
| Python 3.14.7 | Same suite | 107 passed |

Reproduce from the repository with each selected interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The source uses Python 3.10+ syntax and POSIX APIs, including `fcntl`, `pwd`, directory descriptors, and no-follow filesystem operations. The table records local validation. Hosted macOS 15 arm64 validation is reported by the PR workflow described below. Python 3.10, other interpreter versions, Linux, and other macOS versions/architectures remain outside this recorded scope. Native Windows is unsupported. No browser behavior is involved in these checks.

These tests establish specific boundaries, review persistence, publication/refusal behavior, and scoring calculations. They are not measurements of real-world source recall, user effort, writing quality, or trust.

## Pull-request CI

The [CI workflow](../.github/workflows/ci.yml) targets macOS 15 arm64 with Python 3.11, 3.12, and 3.14 on every pull request. Each job executes the complete unittest suite and automated smoke check. Its uploaded evidence records the actual Python patch version, operating system, architecture, checked-out commit, logs, smoke assertions, and cleanup result. Read the run's checks and artifacts to establish success for that revision; the local counts above do not imply that a hosted run passed.

The [CI evidence guide](smoke-test.md#github-actions) describes artifact contents and retention. Hosted CI does not exercise agent-written room documents, human review, desktop skill discovery, live connectors, or browser behavior.

## Repeatable smoke check

The [smoke-test guide](smoke-test.md) documents the automated CLI lifecycle check and a staged path for agent room preparation and drafting. Run `scripts/smoke_test.py run --output /path/to/new/local/evidence` to exercise this checkout, or add `--skill-dir` to select an installed copy. Each invocation records its own results and target identities; smoke assertions are reported separately from the unittest counts above.

The harness uses disposable synthetic sources and scripted approvals. Its scanner access evidence covers selected Python APIs, not OS-level tracing or the agent's own reads. The automated path checks CLI mechanics; the staged path requires separate agent work and artifact review. Neither establishes human approval quality, improved source recall, or product value. It does not expand the verified platforms or client behavior recorded here.

## Invocation scope

The evaluated agent path is an explicit instruction naming the skill and its `SKILL.md` path in a Codex session hosted by the desktop app. The agent reads the skill/contract, invokes the Python scanner and review CLI, prepares the room, and uses authorization already supplied for drafting. The controlled comparison below exercises that path on authorized repository material.

Example:

```text
Use $project-room-intake at /absolute/path/project-room-intake/SKILL.md.
Set up a room for [deliverable] from exact files [A] and [B].
You may discover metadata; present candidates before using their contents.
```

State additional source-use, hashing, or drafting authorization when intended; the example above grants only metadata discovery. Keep the output room separate from source inputs.

This evidence does not test installation/discovery in the desktop UI, implicit skill selection, Codex CLI installation, ChatGPT clients, other agents, or live connectors. `agents/openai.yaml` declares UI metadata and an invocation policy; its presence does not establish those client behaviors. The configuration is unchanged.

## Bounded comparison

The recorded comparison uses a fixed repository snapshot, ordinary bounded intake, and explicit skill use. Criteria and important/relevant source labels are frozen before either run. The [trial contract](trial-contract.md) defines the format, metric calculations, unknown values, and reproduction commands.

The [November 10 comparison](../evaluations/2026-11-10/README.md) records frozen criteria, observations, scores, execution identities, and both handoffs. Both arms used all four predefined important sources, nominated no irrelevant candidates, and reported matching versions and zero clarification turns. Observed wall time was 143.475 seconds for ordinary intake and 258.134 seconds for skill intake; the latter also produced the room artifacts. Human corrections and effort were unmeasured.

Interpretation must retain the task-specific relevance judgments, small sample, source familiarity, shared authorization, model variability, timing effects, and absence of human measurements. This comparison demonstrated no source-selection advantage on this task. Counts and wall time do not establish a causal or general advantage. No numerical gain target or broader benefit claim follows from it.
