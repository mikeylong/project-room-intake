# Project Room Intake

Project Room Intake helps Codex get the right source material in place before it starts writing.

It is useful when the work depends on more than one file, folder, export, transcript, or shared source. Instead of jumping straight into a memo, PRD, audit, report, or research brief, the skill creates a short intake step: what sources exist, which ones look useful, what may be stale or duplicated, and what still seems missing.

The value is simple: better source coverage, fewer accidental assumptions, and a clear moment for human review before drafting begins.

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

Project Room Intake is designed to slow down that first step just enough to make the rest of the work more trustworthy.

## What It Does

The skill asks for clear boundaries first: what topic the room is for, where to look, what dates matter, and what counts as an authoritative source.

Then it creates a candidate source list from the approved locations. It does not roam through broad folders or silently import everything it can find.

After discovery, it gives the user a reviewable set of artifacts:

- A list of candidate sources.
- A summary of what was found and what was excluded.
- Duplicate and stale-file hints.
- Notes about missing context.
- A clear next step: approve sources, add more locations, or stop.

Only after that review should Codex use the material for synthesis or drafting.

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

## How To Ask For It

Example:

```text
Set up a project room for a board memo from ~/Deals/Acme and this Drive export manifest. Do not draft yet.
```

Another example:

```text
Use project-room-intake for a PRD on onboarding improvements. Look in ~/Research/Onboarding and ~/Product/Plans. I want to review the source list before you write anything.
```

The important part is giving Codex a bounded place to look. The skill is intentionally cautious about broad searches.

## Why The Review Step Matters

The best draft is usually not the one produced fastest. It is the one based on the right material.

Project Room Intake gives the human a chance to catch the source problem early: wrong folder, missing export, stale deck, duplicated notes, or a source that should be excluded. That makes the final writing more grounded and easier to trust.
