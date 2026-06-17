---
name: is-it-live
description: Use when the user asks where their code stands, what is actually live vs stuck, whether recent work made it to production, what is deployed, or any confusion about commits/branches/worktrees/deploys. Triggers - "is it live", "is X live yet", "where is my code", "where does my code stand", "did my work ship", "what's deployed", "deployment status", "what's actually in production", "what's stuck". Read-only, plain-English + visual map from dirty working tree to live production.
---

# is-it-live

A read-only map of where every recent change stands, from "I just typed it" (dirty
working tree) to "real users see it" (live in production). Built for a builder who
wants to KNOW what is live and what is stuck without juggling git, GitHub, and the
hosting dashboard in their head.

## The one hard rule

This tool is **read-only with respect to the repo and the deploys**. It never runs
`git add/commit/push/merge/checkout/reset`, never triggers a deploy, never edits
tracked files. Its only writes: an HTML report into a cache directory (`--html`),
and a starter config file when the user explicitly asks for `--init`. (`--fetch`
updates local remote-tracking refs, same as `git fetch`; running `git status` may
refresh git's internal stat cache in `.git/index`, as any `git status` does.)
If the user wants to ACT on what it shows (open a PR, merge, deploy), that is a
separate, explicit step using the repo's normal shipping workflow or another
skill built for changing git/deploy state.

## The Ladder (the mental model the output is built on)

Every change climbs these rungs. It is only truly done at rung 8.

```
 8  LIVE         proven inside the production build real users get
 7  DEPLOYED     in a deployed build, but not (fully) production
 6  MERGED       folded into the deploy branch (usually `main`)
 5  IN A PR      proposed to merge, waiting on review/tests
 4  PUSHED       on GitHub, but on a side branch
 3  COMMITTED    saved, but only on this machine
 2  STAGED       marked to be saved
 1  WORKING TREE raw edits, saved nowhere
```

Plus side-traps (parked OFF the ladder, not climbing): unmerged branches and
worktrees, **detached HEAD** work (no branch label, easy to lose), and being
ahead/behind GitHub. The tool separates *truly* unmerged branches from branches
that LOOK unmerged but were **squash-merged** (content landed, SHA differs).

## How it proves rungs 7-8 (the honesty model)

Rungs 1-6 are certain (pure local git). Rungs 7-8 are claimed only with evidence:

1. **Version endpoints** (configured): the running app echoes its deployed commit
   SHA. Strongest proof. Supports multiple surfaces (frontend + backend).
2. **GitHub Deployments API** (zero config, needs `gh`): Vercel and many providers
   write deployment records with the exact SHA + success status.
3. **Deploy workflow runs** (zero config, needs `gh`): the newest successful run of
   a GitHub Actions workflow with "deploy" in its name (or `deploy_workflows` in
   config) proves what that pipeline shipped.
4. **URL reachability + bundle fingerprint** (configured): weak — proves something
   is up, not which commit. The tool says "unverified" in this case.

When live state was NOT checked (offline, no config), the tool says **NOT
CHECKED** — it never converts "I didn't look" into "it is not live". Echo that
honesty when presenting results.

## How to run it

The engine is `is_it_live.py` (stdlib Python 3, no dependencies). It lives in the
same directory as this SKILL.md — resolve that directory and call it `$SKILL_DIR`.
After a standard install it is also stable at `~/.claude/skills/is-it-live`
(Claude Code) and `~/.codex/skills/is-it-live` (Codex); both are symlinks into the
cloned repo. Run it against the repo the user is in (or `--path <repo>`).

1. **Default — the full picture for the current repo (network on):**
   ```bash
   python3 "$SKILL_DIR/is_it_live.py"
   ```
   Network checks are read-only (HTTP GETs, `gh` reads, `git ls-remote`). The
   header's "GitHub check" line is verified live via `ls-remote`, so a plain run
   is already authoritative about whether your local picture is current.

2. **When local refs are behind GitHub** (header says "local copy is behind"):
   ```bash
   python3 "$SKILL_DIR/is_it_live.py" --fetch
   ```

3. **HTML dashboard (recommended default in Codex):**
   ```bash
   python3 "$SKILL_DIR/is_it_live.py" --html
   ```
   Include the printed `HTML dashboard: /absolute/path.html` as a clickable
   Markdown file link in the final answer. Add `--open` only when the user asks
   to open it in the browser.

4. **Living dashboard (`--serve`)** — the breathing version of the HTML report: a
   local web page that re-checks the repo on an interval and refreshes itself.
   This runs forever, so do NOT run it yourself inside an agent session — give
   the user the command to run in their own terminal:
   ```bash
   python3 "$SKILL_DIR/is_it_live.py" --serve --fetch --open   # http://127.0.0.1:4756
   ```
   Every stuck item on the page has a "copy fix prompt" button that puts an
   agent-ready instruction on the clipboard — the user pastes it to you and you
   act on it.

5. **Other modes:**
   ```bash
   python3 "$SKILL_DIR/is_it_live.py" --path ~/code/other-repo
   python3 "$SKILL_DIR/is_it_live.py" --no-net   # offline: local ladder only
   python3 "$SKILL_DIR/is_it_live.py" --json     # machine-readable (contract below)
   python3 "$SKILL_DIR/is_it_live.py" --init     # scaffold .is-it-live.json
   ```

Flags: `--path <repo>` `--fetch` `--no-net` `--html [path]` `--open` `--color`
`--json` `--init` `--serve [port]` `--interval N` (serve refresh seconds)
`--limit N` (default 20 recent commits) `--version`.

## The visual layers (HTML / --serve)

The page leads with a huge color-coded verdict sentence, then **The Journey**:
an interactive SVG where the user's work travels left-to-right as labeled
bubbles along a track — YOUR LAPTOP → GITHUB → MAIN → a glowing LIVE zone.
Position IS the status: bubbles that reached the green zone are proven live;
bubbles parked at earlier stations are stuck there. Clicking a bubble tells its
story in plain English and offers a "copy fix prompt" button (an agent-ready
instruction the user can paste back to you — act on it when they do).

- With `components` configured, each bubble is a named part of the project
  ("Owner dashboard", "API backend"...; `importance: core` renders bigger).
  Without components, the newest commits travel as bubbles. When a repo has no
  components configured, suggest adding 3-6 entries in `.is-it-live.json`.
- Production surfaces (version endpoints, deployment records, deploy runs,
  URLs) appear as a status caption under the LIVE zone.
- Everything detailed (per-commit table, branches/worktrees, raw evidence, the
  Ladder) lives in collapsed "Dig deeper" accordions below.

## How to present the result to the user

- Lead with the **headline banner**: is the newest work LIVE, in flight, or not
  shipped — and at which rung. One plain sentence.
- Then the **ONE NEXT ACTION** the tool printed. Exactly one verb. Not five.
- Only go deeper (parked branches/worktrees, per-commit table) if they ask.
- Mirror the tool's confidence wording: "verified live (version endpoint +
  GitHub deployment)" vs "merged; production reachable but build unverified" vs
  "live state NOT CHECKED (offline)". Never upgrade or downgrade its claim.
- If the header says the local copy is behind GitHub, re-run with `--fetch`
  before presenting numbers.

## The --json contract (for agents)

`--json` emits `schema_version: 2` with a computed `verdict` block so agents
don't re-derive judgment:

```json
{
  "schema_version": 2,
  "verdict": {
    "verdict": "live | in_flight | not_shipped | unknown | empty",
    "confidence": "verified | proof_unresolved | reachable_unverified | not_checked | unreachable",
    "headline": "same sentence the human banner shows",
    "banner_kind": "ok | info | warn",
    "problems": ["red-flag strings: CI red, prod URL down, deploy divergence"],
    "next_action": {"kind": "push|open_pr|pr|deploy|commit|none|other", "target": "...", "text": "...", "done": false},
    "newest": {"sha": "...", "rung": 6, "rung_label": "MERGED", "action": "..."}
  },
  "classified": [{"sha": "...", "full_sha": "...", "rung": 6, "plain": "...", "action": "..."}],
  "branch_split": {"truly_unmerged": [], "squash_merged": [{"name": "...", "pr": 68}]},
  "proofs": [{"label": "frontend", "source": "version-endpoint", "sha_raw": "..."}],
  "surfaces": [{"label": "frontend", "status": "match|behind|ahead|diverged|unresolved|up|down", "lag": null}],
  "components": [{"name": "...", "rung": 6, "rung_label": "MERGED", "dirty_files": 0, "last": {"sha": "..."}}]
}
```

Read `verdict.verdict` + `verdict.confidence` first; everything else is detail.
`confidence: proof_unresolved` means a production surface named a deployed commit
that is not in local history — re-run with `--fetch` before trusting rungs 7-8.
`--json --html` does both: the JSON gains an `html_report` path field.

## Per-repo config (what makes the live checks richer)

Zero config already gives the full local ladder + GitHub-based proof (deployments,
squash detection, PR state) when `gh` is installed. Config adds production URLs
and version endpoints:

- **Preferred: repo-local `.is-it-live.json`** in the repo root (portable, works in
  worktrees, travels with the repo). Scaffold with `--init`.
- Central fallback: `$SKILL_DIR/config.json`, keyed by absolute repo path
  (gitignored; copy `config.example.json` and edit).

See `config.example.json` for the full field guide. The single best upgrade for
any repo: a version endpoint echoing the deployed commit SHA — it makes "is it
live" provable from anywhere. Suggest adding one when it's missing.

## How it complements other skills

This is the **read-only map**; it never moves anything. Natural flow: run
`/is-it-live` to see what's parked, use the repo's normal shipping or deploy
workflow to move it, then re-run `/is-it-live` to confirm it climbed.

## Known limits (state plainly, don't hide)

- Squash-merge detection needs `gh` + network (it joins against merged PRs). In
  `--no-net` runs, squash-merged work can still read as "not merged" — the output
  says live state was not checked; don't present offline rungs as the full story.
- Lookback bounds: squash detection covers the last ~200 merged PRs; containment
  checks scan the most recent ~300 commits per ref (~50 for the per-branch scan,
  at most 100 branches deep-scanned). A branch squashed further back than that
  can still read as "truly unmerged".
- `git cherry` is deliberately NOT used for squash detection: it false-negatives
  on multi-commit squashes (verified). Don't "simplify" back to it.
- A successful deploy-workflow run proves that pipeline shipped that SHA; a later
  manual rollback outside CI would not show up. Version endpoints win conflicts.
- For VM/self-hosted frontends without a version endpoint, the exact deployed
  commit is often unprovable from this machine. The tool says so rather than
  guessing.
- Deployment records prove the production *deployment* succeeded; if a provider
  serves a stale cache in front of it, the bundle fingerprint can help spot that.
