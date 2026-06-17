# Personal Agent Skills

Portable personal skills for Codex and Claude Code.

## Skills

- `capture-intent`: refine and capture project intent in a checkable `.intentions.md` file
- `check-intent`: review current work against an intentions file
- `create-goal-prompt`: clarify rough work and produce an optimized native Codex `/goal` prompt
- `explain-work`: explain recent agent work in plain English
- `feature-pages`: create evidence-backed, codebase-specific explainer and announcement pages for shipped work
- `is-it-live`: map recent work from dirty local changes through GitHub, deploys, and production

## Install

From this repo:

```bash
./install.sh
```

The installer links each skill into:

- `~/.codex/skills/<skill>`
- `~/.claude/skills/<skill>`

Existing local skill folders are moved to `~/.agent-skill-backups/<timestamp>/` before linking.

After installing, start a new Codex or Claude Code session so skill metadata is reloaded.

To verify the links without changing anything:

```bash
./install.sh --verify
```

## Usage

Invoke the same skill by the runtime's native syntax:

```text
Codex:      Use $capture-intent to capture this plan.
Codex:      Use $create-goal-prompt to turn this into a /goal prompt.

Claude Code: /capture-intent Capture this plan.
Claude Code: /create-goal-prompt Turn this into a /goal prompt.
```

The skill source of truth stays in this repo. The home-directory entries are symlinks, so edits here are picked up by new sessions after install/link verification.

## New Machine Setup

```bash
git clone https://github.com/mendy-survai/personal-agent-skills.git ~/code/personal-agent-skills
cd ~/code/personal-agent-skills
./install.sh
```

Codex uses the `agents/openai.yaml` metadata. Claude Code should ignore that extra metadata folder and load the `SKILL.md` files by slash command.
