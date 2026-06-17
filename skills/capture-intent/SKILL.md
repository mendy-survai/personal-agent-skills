---
name: capture-intent
description: Use after planning a feature, project slice, workstream, or implementation direction when the user asks to capture intent, preserve what they meant, talk through or refine intent, create/update a .intentions.md file, make future drift checks possible, or prepare a captured-intent handoff for a later /goal prompt. Starts with an inferred intent and can run a focused Q&A refinement loop before writing a scoped plain-English intentions file.
---

# Capture Intent

## Purpose

Create a feature-specific intentions document that preserves the user's plain-English intent before implementation drifts into technical detail.

This skill captures what the user is trying to make true. It is not a technical design doc, task list, or implementation contract.

It can also run a short conversational refinement pass before writing, especially when the user wants to think through the project, recover intent from an existing session, or make sure the future check has the right thing to protect.

## Pair Contract

This skill writes the intent record. `check-intent` later reads that record to decide whether current work still honors it. `create-goal-prompt` can later use the captured intent as source truth for a focused `/goal` prompt.

Write the file so a future agent can compare work against it without needing the original conversation. Capture the human outcome, the boundaries, and the drift signals clearly enough to be checked later or carried into a goal session.

## Core Rule

Create one intentions file per plan, feature, or major workstream. Do not append unrelated intentions to a single master file.

Prefer a filename like:

```text
docs/intentions/<feature-slug>.intentions.md
```

If the repo already has a clearer nearby convention, use that convention. Do not overwrite an existing intentions file. If the file already covers the same workstream, update it as a revision and preserve the change history. If it covers a different workstream, create a new file.

## Opening Read

Start by stating what you think the intent is before writing anything, unless the user explicitly asks for a no-discussion update and the intent is already unambiguous.

Use this shape:

```text
My read of the intent:
<2-4 plain-English bullets about the outcome, audience, must-stay-true principle, and likely drift risk.>

Confidence: High / Medium / Low

Choose one:
A) Yes, write/update the intentions file from this read.
B) Refine it with me first.
C) Correct one thing, then write/update it.
```

If there is not enough context for even a low-confidence read, ask one grounding question instead of guessing.

Do not write or update an intentions file after offering this choice until the user confirms A, gives a correction that clearly implies C, or explicitly tells you to proceed.

## Refinement Mode

Use refinement mode when the user chooses it, asks to talk through the intent, invokes this skill on an existing session, or when the initial read has material ambiguity.

### How To Refine

- Re-ground in the current workstream in one sentence before asking.
- Ask one question at a time.
- Explain why the question matters in plain English.
- Push once on vague words such as "better", "seamless", "robust", "agentic", "audit-ready", or "simple" by asking what observable outcome would prove the word true.
- If two plausible intents are competing, name both, recommend the one that seems truest to the user's stated priorities, and ask the user to choose.
- After each answer, update the working read in 2-4 bullets before deciding whether another question is needed.

### Good Refinement Questions

Choose only the questions that are still unanswered:

- "What human outcome should still be true if every implementation detail changes?"
- "Who is this really for: the end user, the owner, a reviewer, a future agent, or someone else?"
- "What would make you say, 'technically it works, but it missed the point'?"
- "What is the narrowest version of this intent that would still be worth protecting?"
- "What tempting improvement would actually be drift?"
- "What decision should a future agent stop and ask you about instead of making silently?"

### Stop Conditions

Stop refining and offer to write/update the file when:

- The user approves the current read
- The must-stay-true principles and drift warning signs are clear
- The remaining uncertainty is an implementation detail, not an intent issue
- The user asks to stop questioning and capture the best current version

When refinement mode was used, present a final "Ready to capture?" summary and wait for confirmation before writing.

## Workflow

### 1. Gather The Intent

Use the strongest available sources, in this order:

- Explicit user statements in the current conversation
- The plan, issue, PR, design note, or handoff the user referenced
- Relevant local files that clarify the workstream
- Careful inference from current work, only when the user has not provided enough context

Infer:

- What the user wants the work to accomplish
- Why it matters
- Who it is for
- What success would look like in human terms
- What should stay true even if the technical plan changes
- What would count as drift

Ask clarifying questions only through the opening read or refinement mode. Prefer making a clearly labeled assumption over interrupting for small uncertainties.

### 2. Separate Intent From Implementation

Keep these distinct:

- Intent: the human outcome, product direction, user experience, and owner priorities
- Implementation: architecture, libraries, data models, tests, or technical sequencing

Technical choices may be mentioned only when they protect or threaten the intent.

### 3. Make The Intent Checkable

Each intention should be concrete enough for `check-intent` to evaluate later.

Good intention bullets:

- Name the user, owner, or audience affected
- Describe an outcome, experience, or principle that must remain true
- Include examples of drift, especially tempting technical shortcuts

Weak intention bullets:

- Restate implementation steps
- Say only "make it better" or "keep quality high"
- Depend on hidden context from the original conversation

### 4. Create Or Update The File

Use this structure:

```markdown
# <Feature Or Workstream> Intentions

Status: Active
Created: YYYY-MM-DD
Last Updated: YYYY-MM-DD
Source: <conversation, issue, plan, PR, branch, or file path>

## Plain-English Intent

<A short explanation of what the user wants this work to make true.>

## Why This Matters

<The owner/user reason this work matters.>

## Who This Is For

<The primary user, owner, reviewer, customer, or future agent this work must serve.>

## Success Looks Like

- <An observable human/product outcome, not just a passing technical check>

## What Must Stay True

- <Non-negotiable intent-level principle>

## Technical Freedom

<What the agent may change freely as long as the intent is preserved.>

## Non-Goals

- <What this work is not trying to become>

## Drift Warning Signs

- <Signs that the implementation is leaving the original intent>

## Changes That Need Discussion

- <Intent-level changes the agent must not make silently>

## Assumptions To Recheck

- <Important assumption made while capturing the intent, or "None">

## Change Notes

- YYYY-MM-DD: Initial intention captured from planning.
```

### 5. Update Existing Intentions Carefully

When updating an existing intentions file:

- Keep the original intent visible unless the user explicitly replaces it
- Add a dated note explaining what changed and why
- Move superseded details into `Change Notes` instead of silently deleting them
- If the new direction is really a different workstream, create a new file and cross-reference the old one

### 6. Report Back

After writing the file, summarize:

- Where the file is
- The intent in one or two sentences
- Any clarifying assumptions
- How to use it with `check-intent` in later sessions

### 7. Optional Goal Handoff

If the user asks for next steps, goal-session prep, or a `/goal` prompt, offer a compact handoff instead of turning this skill into a task planner. Do not generate a full goal prompt unless the user explicitly asks; hand off to `create-goal-prompt` for execution shaping.

Use this shape:

```markdown
## Goal Prompt Seed

Intent source: `<path to .intentions.md or "current conversation">`

Protected intent:
- <human outcome or must-stay-true principle the goal must preserve>

Likely goal:
- <next bounded executable slice, if clear>

Non-goals:
- <things the goal should not become>

Drift risks:
- <tempting mistakes to avoid>

Open execution questions:
- <only questions needed before a strong /goal run, or "None">
```

If there is no obvious next executable slice, say that the intent is captured and recommend deciding the next slice before creating a goal prompt.

## Verification

Before finishing, check:

- The user confirmed the opening read or approved the refined read before any file was written
- The file is scoped to one workstream, not a master ledger
- The `Plain-English Intent`, `What Must Stay True`, `Technical Freedom`, `Non-Goals`, `Drift Warning Signs`, and `Changes That Need Discussion` sections are all populated
- At least one drift warning sign describes a realistic tempting mistake
- Technical details appear only when they protect or threaten the intent
- A future `check-intent` pass could evaluate the current work against the file without rereading the whole planning conversation
- Any Goal Prompt Seed stays intent-level except for the likely next slice and open execution questions

## Style

Write for a thoughtful non-engineer. Use plain language, short sections, and concrete examples of drift. Avoid buzzwords and technical detail that does not protect the intent.
