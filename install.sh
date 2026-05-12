#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$ROOT/skills"
BACKUP_DIR="$HOME/.agent-skill-backups/$(date +%Y%m%d-%H%M%S)"

targets=(
  "${CODEX_HOME:-$HOME/.codex}/skills"
  "${CLAUDE_HOME:-$HOME/.claude}/skills"
)

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "Missing skills directory: $SKILLS_DIR" >&2
  exit 1
fi

link_skill() {
  local src="$1"
  local target_root="$2"
  local skill
  local dest
  local target_label

  skill="$(basename "$src")"
  dest="$target_root/$skill"
  target_label="$(basename "$(dirname "$target_root")")"

  mkdir -p "$target_root"

  if [[ -L "$dest" ]]; then
    if [[ "$(readlink "$dest")" == "$src" ]]; then
      echo "Already linked: $dest -> $src"
      return 0
    fi
    mkdir -p "$BACKUP_DIR"
    mv "$dest" "$BACKUP_DIR/${target_label}-${skill}.symlink"
  elif [[ -e "$dest" ]]; then
    mkdir -p "$BACKUP_DIR"
    mv "$dest" "$BACKUP_DIR/${target_label}-${skill}"
  fi

  ln -s "$src" "$dest"
  echo "Linked: $dest -> $src"
}

for src in "$SKILLS_DIR"/*; do
  [[ -d "$src" ]] || continue
  [[ -f "$src/SKILL.md" ]] || continue

  for target_root in "${targets[@]}"; do
    link_skill "$src" "$target_root"
  done
done

if [[ -d "$BACKUP_DIR" ]]; then
  echo "Backups saved in: $BACKUP_DIR"
fi

echo "Done."
