---
name: check-intent
description: Use when the user wants to review current work against a project-specific .intentions.md file, check whether implementation has drifted, audit alignment before continuing, or decide whether changed work preserves or redefines the original intent. Produces a plain-English alignment review, not a full code review.
---

# Check Intent

## Purpose

Review current work against a feature-specific intentions file and explain whether the work still honors the user's original intent.

This skill steers technical choices back toward the intended human outcome. It does not replace engineering judgment, tests, or code review.

## Pair Contract

`capture-intent` writes the intent record. This skill reads that record and compares it to current work.

Do not invent a new intent to make the work look aligned. If the work implies a different intent, name that directly and ask for a decision.

## Core Rule

The agent may adapt technical choices, but it must not silently redefine the user's intent.

If the work implies a changed intent, pause and make that explicit before recommending more implementation.

## Workflow

### 1. Find The Relevant Intentions File

Prefer an explicitly named `.intentions.md` file. If none is named, search likely locations such as:

- `docs/intentions/`
- `docs/architecture/`
- The current feature or plan directory

If multiple files could apply, choose the closest match and state the assumption. Ask only if choosing wrong would make the review misleading.

If no intentions file exists, say that clearly and recommend running `capture-intent` before doing an alignment review. If the current intent is not obvious, recommend `capture-intent` refinement mode so the user can confirm what should be protected before any file is written. Do not create or update an intentions file unless the user asks.

### 2. Inspect Current Work

Review only what is needed to understand alignment:

- Current branch/worktree
- `git status --short`
- Changed files or diff summary
- Relevant plan, issue, or verification notes

Do not turn this into a full technical review unless the user asks for one.

Ignore unrelated dirty files when they do not affect the workstream being checked. State that they were present if the distinction matters.

### 3. Compare Work To Intent

Assess in plain English:

- What the original intent says should stay true
- What the current work appears to be doing
- Where the work is aligned
- Where the work may be drifting
- Whether any drift is a harmless implementation detail or a real intent change

Use these labels:

- `Aligned`: current work honors the intent
- `Watch item`: not wrong yet, but easy to drift if continued
- `Drift risk`: the work is pulling away from the intent and should be adjusted
- `Intent change`: the work appears to redefine the original goal, audience, boundary, or success condition

Treat technical changes as acceptable when they stay inside `Technical Freedom` and preserve `What Must Stay True`. Treat them as drift when they undermine must-stay-true principles, expand a non-goal, change the intended audience, or make a discussion-required change silently.

### 4. Identify Decisions That Need The User

If the agent's current direction changes the intent, say so clearly:

```text
This appears to change the original intention.

Original intent:
...

New direction implied by the work:
...

Why the agent may be trying this:
...

Decision needed:
Preserve the original intent, revise the intent, or treat this as a temporary implementation detail.
```

Do not update the intentions file unless the user confirms the intent has changed or explicitly asks for an update.

### 5. Recommend The Next Step

End with one recommended next step:

- Continue as planned
- Adjust the implementation while preserving the intent
- Pause for a user decision
- Revise the intentions file, if the user has confirmed the intent changed

Recommend exactly one primary next step. You may include one short supporting note if there is a useful follow-up.

## Output Shape

Use this structure unless the user asks for something else:

```text
Intent Being Protected
Evidence Reviewed
Current Work In Plain English
Alignment Check
Possible Drift
Decision Needed
Recommended Next Step
```

When there is no user decision needed, write `Decision Needed: None`.

## Verification

Before finishing, check:

- The selected intentions file is named, or the absence of one is explicit
- If no file exists, the recommendation says whether direct capture or refinement mode is the better next step
- The evidence reviewed is enough to support the alignment claim without becoming a full code review
- Each possible drift item is classified as `Watch item`, `Drift risk`, or `Intent change`
- Harmless implementation changes are not overstated as intent drift
- Any actual intent change ends with a clear user decision, not more silent implementation

Keep the tone calm and direct. The goal is to restore orientation, not create anxiety.
