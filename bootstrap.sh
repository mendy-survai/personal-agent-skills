#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${AGENT_SKILLS_REPO_URL:-https://github.com/mendy-survai/personal-agent-skills.git}"
TARBALL_URL="${AGENT_SKILLS_TARBALL_URL:-https://github.com/mendy-survai/personal-agent-skills/archive/refs/heads/main.tar.gz}"
INSTALL_DIR="${AGENT_SKILLS_INSTALL_DIR:-$HOME/.agent-skills/personal-agent-skills}"

say() {
  printf '%s\n' "$*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    say "Missing required command: $1"
    say "Please install it, then run this installer again."
    exit 1
  fi
}

clone_or_update_with_git() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    say "Updating existing skills repo at $INSTALL_DIR"
    if ! git -C "$INSTALL_DIR" pull --ff-only; then
      say "Could not update automatically, so I will use the existing copy."
    fi
    return 0
  fi

  if [[ -e "$INSTALL_DIR" ]]; then
    local backup
    backup="$HOME/.agent-skill-backups/bootstrap-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup"
    mv "$INSTALL_DIR" "$backup/personal-agent-skills"
    say "Moved existing $INSTALL_DIR to $backup/personal-agent-skills"
  fi

  mkdir -p "$(dirname "$INSTALL_DIR")"
  say "Downloading Mendy's agent skills to $INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
}

download_without_git() {
  need_cmd curl
  need_cmd tar

  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  say "Downloading Mendy's agent skills to $INSTALL_DIR"
  curl -fsSL "$TARBALL_URL" | tar -xz -C "$tmp"

  if [[ -e "$INSTALL_DIR" ]]; then
    local backup
    backup="$HOME/.agent-skill-backups/bootstrap-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup"
    mv "$INSTALL_DIR" "$backup/personal-agent-skills"
    say "Moved existing $INSTALL_DIR to $backup/personal-agent-skills"
  fi

  mkdir -p "$(dirname "$INSTALL_DIR")"
  mv "$tmp/personal-agent-skills-main" "$INSTALL_DIR"
}

say "Installing Mendy's agent skills."

if command -v git >/dev/null 2>&1; then
  clone_or_update_with_git
else
  download_without_git
fi

"$INSTALL_DIR/install.sh" "$@"
"$INSTALL_DIR/install.sh" --verify "$@"

cat <<EOF

Installed Mendy's agent skills.

Next step:
  Start a new Codex or Claude Code session, then ask naturally:
  - Use is-it-live to tell me whether this repo is deployed.
  - Use feature-pages to write a launch page for this shipped feature.
  - Use capture-intent before we start building this.

Installed from:
  $INSTALL_DIR
EOF
