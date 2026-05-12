# Personal Agent Skills

Portable personal skills for Codex and Claude Code.

## Skills

- `capture-intent`: refine and capture project intent in a checkable `.intentions.md` file
- `check-intent`: review current work against an intentions file
- `explain-work`: explain recent agent work in plain English

## Install

From this repo:

```bash
./install.sh
```

The installer links each skill into:

- `~/.codex/skills/<skill>`
- `~/.claude/skills/<skill>`

Existing local skill folders are moved to `~/.agent-skill-backups/<timestamp>/` before linking.

## New Machine Setup

```bash
git clone <private-repo-url> ~/code/personal-agent-skills
cd ~/code/personal-agent-skills
./install.sh
```

Codex uses the `agents/openai.yaml` metadata. Claude Code should ignore that extra metadata folder.
