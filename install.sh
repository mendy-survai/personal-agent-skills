#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$ROOT/skills"
BACKUP_DIR="$HOME/.agent-skill-backups/$(date +%Y%m%d-%H%M%S)"
MODE="${1:-install}"

targets=(
  "${CODEX_HOME:-$HOME/.codex}/skills"
  "${CLAUDE_HOME:-$HOME/.claude}/skills"
)

usage() {
  cat <<EOF
Usage: ./install.sh [--verify]

Links each skill into:
  ${CODEX_HOME:-$HOME/.codex}/skills
  ${CLAUDE_HOME:-$HOME/.claude}/skills

Use --verify to check that installed links point back to this repo.
EOF
}

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "Missing skills directory: $SKILLS_DIR" >&2
  exit 1
fi

case "$MODE" in
  install|"") ;;
  --verify|verify) MODE="verify" ;;
  -h|--help) usage; exit 0 ;;
  *)
    usage >&2
    exit 2
    ;;
esac

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

verify_skill() {
  local src="$1"
  local target_root="$2"
  local skill
  local dest
  local actual

  skill="$(basename "$src")"
  dest="$target_root/$skill"

  if [[ ! -L "$dest" ]]; then
    echo "Missing link: $dest" >&2
    return 1
  fi

  actual="$(readlink "$dest")"
  if [[ "$actual" != "$src" ]]; then
    echo "Wrong link: $dest -> $actual (expected $src)" >&2
    return 1
  fi

  if [[ ! -f "$dest/SKILL.md" ]]; then
    echo "Missing SKILL.md through link: $dest" >&2
    return 1
  fi

  echo "Verified: $dest -> $src"
}

failed=0

for src in "$SKILLS_DIR"/*; do
  [[ -d "$src" ]] || continue
  [[ -f "$src/SKILL.md" ]] || continue

  for target_root in "${targets[@]}"; do
    if [[ "$MODE" == "verify" ]]; then
      verify_skill "$src" "$target_root" || failed=1
    else
      link_skill "$src" "$target_root"
    fi
  done
done

if [[ "$MODE" == "verify" ]]; then
  if [[ "$failed" -ne 0 ]]; then
    echo "Verification failed." >&2
    exit 1
  fi
  echo "All skill links verified."
  exit 0
fi

if [[ -d "$BACKUP_DIR" ]]; then
  echo "Backups saved in: $BACKUP_DIR"
fi

echo "Done. Start a new Codex or Claude Code session to pick up skill metadata changes."
