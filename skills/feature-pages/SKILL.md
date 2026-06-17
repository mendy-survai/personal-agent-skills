---
name: feature-pages
description: Use when asked to create, update, or regenerate project-native feature documentation, work summaries, launch pages, explainer pages, marketing announcements, release-story pages, or "what shipped" pages for a completed capability. Produces evidence-backed, repo-specific output that follows the current codebase/company style instead of assuming a fixed product, brand, path, or visual system.
---

# Feature Pages

## Purpose

Create project-native feature pages that explain what shipped, why it matters, and what evidence proves it works.

This skill is portable across repos. It must adapt to the current codebase's product language, design system, documentation conventions, audience, and release process. It must not carry over another company's tone, paths, product names, or styling assumptions.

## Core Rule

Tell the strongest true story the repo can support.

Every claim must trace back to code, tests, docs, commits, PRs, product artifacts, or user-provided facts. Do not invent outcomes, metrics, customer claims, screenshots, brand language, or mechanisms to make the page feel more polished.

## Default Output

Unless the repo has a different convention, produce a two-piece set:

- `docs/features/<feature-slug>.html`: a plain-English explainer for someone trying to understand the capability.
- `docs/features/<feature-slug>-announcement.html`: a more outward-facing announcement or launch story.

Adapt this when the repo clearly uses another system, such as Markdown docs, MDX, a docs app route, a marketing site, a changelog format, or a feature index. If the right location is ambiguous and the choice matters, ask one short question. Otherwise choose the least surprising nearby convention and state it.

## Workflow

### 1. Resolve The Repo Convention

Inspect the current project before writing:

- Existing feature pages, launch notes, docs pages, changelogs, or marketing pages
- `docs/`, `site/`, `app/`, `pages/`, `content/`, `src/`, `README`, and route conventions
- Design tokens, CSS variables, component libraries, fonts, layout patterns, and brand voice
- Any project-specific instructions such as `AGENTS.md`, `README`, `CONTRIBUTING`, or docs guidelines

If a local feature-page spec exists, read it and follow it. If existing examples are good, match their structure and quality while rewriting the content from the current feature's facts.

### 2. Confirm The Feature Is Ready

Decide whether the page should describe a finished capability, an internal preview, or a draft.

Use the repo's own proof signals: tests, deployment notes, merged PRs, release docs, feature flags, status files, screenshots, product specs, or `is-it-live` output when available. If you cannot confirm the capability is finished, do not present it as launched. Either stop and explain what is unverified, or label the output clearly as draft/internal preview if the user asked for a draft anyway.

### 3. Build A Fact Ledger

Gather the minimum evidence needed for truthful pages:

- User-facing outcome: what changed for the user, buyer, operator, developer, or team
- Mechanism: how the feature actually works at a high level
- Guarantees: validations, fallbacks, gates, permissions, reliability behavior, or constraints
- Proof: tests, source files, docs, deploy/release evidence, screenshots, or real examples
- Limits: what the feature does not do, what remains manual, or what is still experimental

Keep a short source list while working. The final page should either include a compact sources/evidence footer or make the evidence obvious through linked repo references, depending on the repo's convention.

### 4. Inherit The Project Style

Styling must be company- or codebase-specific:

- Reuse existing design tokens, colors, typography, spacing, components, and layout idioms when present.
- Match the current product's voice: technical, executive, playful, clinical, operational, developer-first, etc.
- Keep claims and visuals appropriate to the audience. Internal engineering summaries can name implementation details; public announcements should translate them into user value.
- If the repo has no strong visual system, use a restrained single-file style with semantic HTML, responsive layout, accessible contrast, and no external dependencies unless the repo already uses them.

Do not reuse another project's styling, content sections, terminology, local paths, or product metaphors unless the current repo itself uses them.

### 5. Write The Explainer

The explainer should help a smart reader understand the capability without reading the whole codebase.

Prefer sections like:

- What changed
- Who it is for
- How it works
- What is now safer/faster/easier
- Evidence and verification
- Known limits or next steps

For technical features, include the core workflow and the real guardrails. For product features, include the user journey and operational impact. For developer tooling, include the command, integration point, and proof that it works.

### 6. Write The Announcement

The announcement should make the work legible and compelling without overstating it.

Prefer sections like:

- Short launch headline
- Problem before
- What is new
- Why it matters
- Concrete example or scenario
- Proof points
- CTA or next action

Use marketing energy only where the evidence supports it. Replace vague hype with specific changed behavior.

### 7. Update Indexes

If the repo has an index, nav config, changelog, feature list, sitemap, sidebar, or docs manifest, update it. Do not invent a new index if the repo has no convention and the user did not ask for one.

### 8. Verify The Output

Run the cheapest meaningful checks:

- For HTML: render in a browser or headless browser and inspect screenshots for overflow, broken layout, missing assets, and mobile behavior.
- For Markdown/MDX/docs apps: run the docs build, lint, typecheck, or local preview command when available and proportionate.
- Check links and source references.
- Re-read the finished copy for unsupported claims.

If rendering or build tools are unavailable, say what you inspected instead.

## Done Criteria

- Output location follows the repo convention or a stated reasonable default.
- The feature is presented with the correct status: launched, internal preview, draft, or unverified.
- Styling and voice are project-native, not copied from another codebase.
- Every meaningful claim is backed by inspected evidence or explicitly marked as user-provided.
- Index/nav files are updated when the repo expects that.
- Render/build verification was run where practical, and any remaining risk is named.

## Public-Release Hygiene

When the pages or skill output may be public, scrub private-only details:

- Avoid secrets, internal credentials, private URLs, customer data, and local absolute paths.
- Prefer repo-relative references.
- Do not expose unpublished roadmap promises unless the user explicitly asks and the repo already treats them as public.
