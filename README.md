# Mendy's Agent Skills

Portable agent skills for Codex and Claude Code.

These are small workflows I use to make AI coding agents more useful: checking
what is actually live, turning shipped work into launch pages, preserving intent,
and explaining what happened in plain English.

## Easiest Install

Paste this into Codex or Claude Code:

```text
Install Mendy's agent skills from https://github.com/mendy-survai/personal-agent-skills, then show me what skills are available.
```

The agent should clone this repo, run the installer, verify the install, and tell
you what to try first.

## One Terminal Command

If you are comfortable pasting a command into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/mendy-survai/personal-agent-skills/main/bootstrap.sh | bash
```

That installs the whole pack into both:

- `~/.codex/skills`
- `~/.claude/skills`

After installing, start a new Codex or Claude Code session so the skill list is
reloaded.

## What You Get

| If you want to... | Use this skill |
| --- | --- |
| Know whether recent code is actually live | `is-it-live` |
| Turn shipped work into an explainer or announcement page | `feature-pages` |
| Capture what you meant before implementation drifts | `capture-intent` |
| Check current work against captured intent | `check-intent` |
| Turn a rough idea into a strong Codex `/goal` prompt | `create-goal-prompt` |
| Understand what an agent just did in plain English | `explain-work` |

## Try It

After install, ask naturally:

```text
Use is-it-live to tell me where this repo stands.
```

```text
Use feature-pages to create a launch page for this completed feature.
```

```text
Use capture-intent before we build this.
```

## Manual Install

If you already cloned this repo:

```bash
./install.sh
```

To see available skills:

```bash
./install.sh --list
```

To install just one skill:

```bash
./install.sh is-it-live
```

To verify the installed links:

```bash
./install.sh --verify
```

## How Install Works

The installer links each skill folder into Codex and Claude Code. Existing local
skill folders with the same names are moved to:

```text
~/.agent-skill-backups/<timestamp>/
```

The default is to install the whole pack. You choose what to use later by asking
Codex or Claude Code for the skill you want.

## For New Machines

```bash
git clone https://github.com/mendy-survai/personal-agent-skills.git ~/code/personal-agent-skills
cd ~/code/personal-agent-skills
./install.sh
```

Codex uses the `agents/openai.yaml` metadata. Claude Code should ignore that
extra metadata folder and load the `SKILL.md` files by slash command.
