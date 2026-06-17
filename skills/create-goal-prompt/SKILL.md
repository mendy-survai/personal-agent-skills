---
name: create-goal-prompt
description: Use when the user wants to turn a rough idea, bug, plan, feature slice, audit, research task, mid-session context, or captured intent/intentions file into a clear optimized native Codex /goal prompt. Asks only execution-shaping questions, uses smart defaults, prints a paste-ready prompt by default, and writes a draft goal file only after approval.
---

# Create Goal Prompt

## Purpose

Turn fuzzy or partially-formed work into a native Codex `/goal` prompt that is bounded, verifiable, and hard to misread.

This skill is an execution-shaping partner. Its main output is a better goal prompt, not code, branch setup, or a worktree. It can start from a rough request, a plan, a bug, a current session, a Goal Prompt Seed from `capture-intent`, or an existing `.intentions.md` file.

## Core Rule

Clarify only what changes the run.

Start with an opinionated read of the goal, then ask the single most useful next question only when the answer would materially change scope, proof, risk, or user intent. Prefer a labeled assumption over another question for minor implementation details.

A strong `/goal` run needs:

- clear objective
- bounded scope
- concrete done criteria
- relevant context and files
- cheap fast-feedback loop
- full verification
- escalation triggers
- expected final report shape

If captured intent exists, treat it as the source of truth for why the work matters, who it serves, what must stay true, non-goals, drift risks, and escalation triggers. Do not re-litigate those unless they conflict with the requested goal session.

## Interaction Style

Borrow the best parts of office-hours and engineering-review style:

- Ask one question at a time.
- Frame questions in outcome terms: what improves, what breaks, what proof matters.
- State the likely misfire for vague, risky, broad, or values-laden goals.
- Recommend a default and explain why.
- Give 2-3 concrete options when a choice would help the user answer faster.
- Smart-skip anything already clear from the user's words or referenced files.
- Push once on vague words like "clean up", "robust", "better", "audit-ready", "simple", "agentic", or "production-ready" by asking what observable proof would make the word true.
- Treat the user's answers as design signal, not form data. After each answer, update the working read before asking the next material question.

Use a host question tool only when it is available, allowed in the current mode, and the question naturally fits a short choice set. Otherwise ask in normal chat.

## Intent-Aware Mode

When the user provides a captured intent, a Goal Prompt Seed, or an `.intentions.md` file:

- Use it as the protected intent source.
- Pull `Objective`, `Out-of-Scope`, `Constraints`, `Escalation triggers`, and the likely misfire from it when possible.
- Ask only for missing execution details: this goal session's slice, required reading or discovery starting points, fast feedback, final verification, and final report shape.
- Include the intent source in `Required Reading / Starting Points`.
- Preserve the distinction between intent and implementation. The goal prompt should execute a bounded slice while protecting the intent, not rewrite the intent record.

## Opening Read

For vague or risky requests, begin with this shape:

```text
My read:
- <what should become true>
- <who or what this is for>
- <likely goal type: implementation / audit / plan-only / research / recovery>
- <likely misfire if we launch too soon>

Confidence: High / Medium / Low

The next question I would ask is:
<one material question>

My default would be <answer/option> because <reason>.
```

For high-confidence requests, use a shorter version:

```text
My read: <one sentence>.

One thing to lock down: <highest-risk missing question>
My default would be <answer/option> because <reason>.
```

If the request is already specific enough, proceed directly to the prompt with labeled assumptions.

## Question Ladder

Use only the questions that are still unresolved. Ask them one at a time.

1. **Intent source:** Is there a captured intent or `.intentions.md` file to protect?
2. **Execution slice:** What should this goal session actually do?
3. **Proof:** What evidence would convince the user this worked?
4. **Scope:** What is in scope, and what would be scope creep?
5. **Starting points:** What files, docs, errors, diffs, examples, branches, commands, or URLs should Codex read first?
6. **Constraints:** What must be preserved, avoided, or escalated?
7. **Fast feedback:** What cheap command, artifact, or review can Codex use after each meaningful slice?
8. **Final verification:** What commands, artifacts, demos, citations, screenshots, or review steps prove completion?
9. **Output shape:** Should final reporting be implementation, broad/audit, or plan-only?
10. **Launch form:** Paste-ready prompt, draft `goal.md`, or handoff to `goalbuddy:goal-prep` if available?

### Good Question Shapes

Use concise decision briefs:

```text
One thing to lock down: <question>

Why it matters: <stakes in one sentence>

1. <recommended answer> (Recommended) - <when this wins>
2. <second answer> - <tradeoff>
3. <third answer, only if useful> - <tradeoff>

My default would be <option> because <reason>.
```

When the answer is open-ended, skip options and ask directly:

```text
What would make you say, "Codex did the work, but it missed the point"?
```

## Readiness Bar

The prompt is ready when these are true:

- The objective fits in one paragraph.
- A stranger could tell what is in and out of scope.
- Done criteria are observable, not vibes.
- Fast feedback is cheap enough to run repeatedly, or the prompt explains how Codex should discover it.
- Full verification is explicit, or the prompt asks Codex to discover the exact command/evidence and record it.
- Required reading or discovery starting points point to concrete files, docs, diffs, URLs, commands, or search paths.
- The likely misfire is addressed by constraints, done criteria, or escalation triggers.

If one missing answer materially changes scope, proof, risk, or user intent, ask. If the missing answer is a minor implementation detail, label the assumption and proceed.

## Goal Prompt Template

Once the request is clear enough, produce the prompt. User approval is required before writing a file or launching another workflow, not before printing a paste-ready prompt that the user already asked for.

```markdown
# Goal: <short descriptive title>

**Output Shape:** Implementation goal — Implemented / Verification / Notes

For plan-only goals, replace the output shape with: `Plan-only — Goal / Assumptions / Execution Plan / Verification Plan / Risks / Decisions`.

## Objective

<What should become true, why it matters, and what this goal deliberately leaves out.>

If an intent source exists, protect it during the run. Treat drift warning signs and changes needing discussion as escalation triggers.

## Required Reading / Starting Points

1. `<path, source, URL, command, or search path>` — <why it matters>
2. `<path, source, URL, command, or search path>` — <why it matters>

## In-Scope

1. **<deliverable>** — <specific work>
2. **<deliverable>** — <specific work>

## Out-of-Scope

- <explicit non-goal>
- <explicit deferral>

## Constraints

- <hard rule or preservation requirement>
- <safety, product, data, or repo constraint>
- If durable loop files already exist or the goal is broad/long-running/recovery-oriented, keep `PLAN.md`, `ATTEMPTS.md`, and `SESSION_NOTES.md` current. Do not create them for a small goal unless they clearly help.

**Escalation triggers:**

- <when Codex should stop and ask>
- <when a local assumption is too risky>

## Done Criteria

Complete when:

- <observable outcome>
- <tests/artifacts/review evidence>
- <final audit or self-check>

## Fast Feedback

Run or check after each meaningful slice:

- <cheap focused command, artifact, review step, or discovery instruction>

Use a `bash` block only when every line inside it is an executable shell command. Never put prose such as "Discover..." inside a `bash` code fence.

## Verification

Run or gather before reporting completion:

- <full verification command, artifact, review step, or discovery instruction>

Use a `bash` block only when every line inside it is an executable shell command. Never put prose such as "Discover..." inside a `bash` code fence.

## Notes For The Runner

- Likely misfire to avoid: <misfire>
- Important assumption: <assumption or None>
```

For research or audit goals, use this shape instead:

```markdown
# Goal: <short descriptive title>

**Output Shape:** Broad goal — Current Truth / Blocked By Reason / Highest-Risk Gaps / Verification / Next Sessions

## Objective

<Question to answer, decision to support, or audit truth to establish. Say what this deliberately leaves out.>

## Required Reading / Starting Points

1. `<path, source, URL, command, or search path>` — <why it matters>

## Evidence Lanes

- <documented intent, source truth, runtime truth, user-visible behavior, or other lane>
- <another lane>

## Out-of-Scope

- <explicit non-goal>

## Constraints

- <source quality, privacy, safety, or product constraint>

**Escalation triggers:**

- <when evidence is missing, contradictory, stale, or too risky to infer>

## Done Criteria

Complete when:

- <observable truth/report outcome>
- <blockers are classified by reason>
- <claims are backed by sources, commands, artifacts, or clear uncertainty>

## Fast Feedback / Evidence

- <cheap focused check, source sample, command, or artifact>

## Verification

- <final commands, source cross-checks, screenshots, citations, artifacts, or review steps>

## Notes For The Runner

- Likely misfire to avoid: <misfire>
- Important assumption: <assumption or None>
```

## Optional File Output

Default to printing the prompt in chat.

Write a file only when the user asks or approves a write. Prefer:

```text
docs/goals/<slug>/goal-draft.md
```

Use `goal.md` only when the user says the prompt is final or asks to prepare an actual goal directory.

## Quality Review Before Final Output

Before showing the final prompt, run this private check and fix weak spots:

- Objective is specific enough.
- Required reading or discovery starting points are concrete.
- Scope and non-goals are explicit.
- Done criteria are observable.
- Fast feedback and verification are runnable, reviewable, or clearly discoverable.
- Any `bash` block contains only real shell commands, not prose instructions.
- Output Shape exactly matches one native `/goal` report shape.
- Escalation triggers cover risky ambiguity.
- Durable loop files are included only when they fit the goal's complexity.
- Prompt uses affirmative instructions.
- The final prompt does not depend on hidden chat context.

If the prompt is still weak, say what is missing and ask the one question that would improve it most.

## Report Back

After producing the final prompt, include:

- `Readiness: <Ready | Ready with assumptions | Needs one more answer>`
- `Best next step: <paste into /goal | write draft goal.md | run goalbuddy:goal-prep if available | capture intent first>`
- Any assumption the runner should recheck.

## Verification

Before finishing, confirm:

- Any file write was explicitly approved.
- The prompt includes Objective, Required Reading / Starting Points, In-Scope or Evidence Lanes, Out-of-Scope, Constraints, Done Criteria, Fast Feedback, and Verification.
- There is exactly one recommended next step.
- The tone stayed practical and conversational, not bureaucratic.
