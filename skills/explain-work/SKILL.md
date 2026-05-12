---
name: explain-work
description: Use when the user asks for a simple, non-technical explanation of what an agent just did, why it was done, what is known vs inferred, where the work stands, whether the session drifted, or what the next step should be. Also use when the user feels lost, unsure where the session is going, or wants a plain-English handoff/resume summary.
---

# Explain Work

## Purpose

Translate recent work into plain English so the user understands what happened, why it matters, and what to do next.

This skill is for orientation, not for new implementation. Do not bury the user in technical details. Connect the work back to the user's goal, intent, and decision-making.

The job is not to defend the agent. The job is to help the user feel re-oriented and able to choose the next move.

## Core Rule

Separate what is known from what is inferred. Do not make incomplete work sound finished, and do not turn a plain-English explanation into a full code review or second implementation plan.

## Choose The Mode

Pick the lightest mode that matches the user:

- **Quick orientation:** The user asks "what happened?" or "what did you do?"
- **Lost or drifted:** The user says they feel lost, confused, drifted, or unsure why the session is going this way
- **Handoff or resume:** The user wants a summary another session or agent can continue from
- **Decision explanation:** The user wants to understand why a path was chosen or what tradeoff was made

If the user sounds disoriented, start with the current goal in one sentence before listing details.

## Workflow

### 1. Reconstruct The Current State Lightly

Use the available context first. For repo work, inspect only what is needed:

- Current branch/worktree
- `git status --short`
- Recent changed files or diff summary
- Relevant verification output, if already run

Do not run broad new verification unless the user asked for verification. If no local evidence is available, use the conversation context and say what you are relying on.

When there are unrelated dirty files, do not let them dominate the explanation. Mention them only if they affect confidence or next steps.

### 2. Explain What Happened

Answer in plain language:

- What was accomplished
- What problem it was trying to solve
- Why this was a reasonable or necessary step
- What changed for the user, product, plan, or workflow

Avoid jargon unless the term is important. If a technical term is necessary, define it briefly.

Use a three-layer explanation:

- **Human outcome:** What this means for the user's goal
- **Concrete change:** What actually changed or was decided
- **Evidence:** How you know

### 3. Separate Done From Not Done

Make the status easy to trust:

- Done: concrete outcomes
- Not done yet: remaining work
- Evidence: tests, screenshots, commands, review output, or file changes
- Uncertainty: anything that has not been verified

Do not make incomplete work sound finished.

If the user is worried about drift, compare the work against the stated goal or intent in the conversation. If a `.intentions.md` file exists or should exist, recommend `check-intent` for a formal drift review instead of pretending this skill is that review.

### 4. Explain Decisions Without Re-Litigating Everything

When explaining a decision:

- Name the constraint or goal that drove it
- Name the reasonable alternative that was not chosen
- Explain the tradeoff in one or two sentences
- Say what evidence would change the decision, if that matters

Do not turn the answer into a sprawling menu of possibilities.

### 5. Recommend The Next Step

Give one recommended next step, not a sprawling menu.

If there are multiple possible actions, classify them:

- Do next: the single best next action and why
- Can wait: useful but not blocking
- Depends on: anything that must happen first

If work should continue in a specific branch, worktree, file, or document, state that clearly.

### 6. Provide A Resume Prompt When Useful

For work that may continue in another session, include a short prompt the user can paste later. The prompt should name:

- The goal
- The current branch/worktree, if relevant
- The most important files or artifacts
- The recommended next step
- Any known uncertainty or verification gap

## Output Shape

Use the smallest structure that gives orientation. For tiny questions, a short paragraph is enough.

For normal explanations:

```text
In Plain English
What Changed
Where Things Stand
Recommended Next Step
Resume Prompt
```

For lost or drifted sessions:

```text
Where We Are
What Changed
What Still Matters
Possible Drift
Recommended Next Step
```

For handoffs:

```text
Current Goal
Current State
Key Files Or Artifacts
Evidence And Gaps
Recommended Next Step
Resume Prompt
```

## Verification

Before finishing, check:

- Every status claim is backed by evidence or labeled as inference
- Done, not done, and unknown are clearly separated
- The recommended next step is singular and actionable
- The answer does not bury the user in raw diffs, logs, or implementation detail
- Any resume prompt has enough context for a future session to continue without rereading the whole thread

Keep the answer concise. The goal is clarity and confidence, not a second technical plan.
