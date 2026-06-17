#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$ROOT/skills"
BACKUP_DIR="$HOME/.agent-skill-backups/$(date +%Y%m%d-%H%M%S)"
MODE="install"
requested_skills=()

targets=(
  "${CODEX_HOME:-$HOME/.codex}/skills"
  "${CLAUDE_HOME:-$HOME/.claude}/skills"
)

usage() {
  cat <<EOF
Usage:
  ./install.sh                 Install every skill
  ./install.sh all             Install every skill
  ./install.sh is-it-live      Install one or more named skills
  ./install.sh --list          Show available skills
  ./install.sh --verify        Verify installed links

Links each skill into:
  ${CODEX_HOME:-$HOME/.codex}/skills
  ${CLAUDE_HOME:-$HOME/.claude}/skills

The default is the whole pack. Choose later by asking Codex or Claude Code for
the skill you want, for example: "Use is-it-live on this repo."
EOF
}

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "Missing skills directory: $SKILLS_DIR" >&2
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    install)
      MODE="install"
      ;;
    all)
      ;;
    --verify|verify)
      MODE="verify"
      ;;
    --list|list)
      MODE="list"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        requested_skills+=("$1")
        shift
      done
      break
      ;;
    -*)
      usage >&2
      exit 2
      ;;
    *)
      requested_skills+=("$1")
      ;;
  esac
  shift
done

should_include_skill() {
  local skill="$1"

  if [[ "${#requested_skills[@]}" -eq 0 ]]; then
    return 0
  fi

  local requested
  for requested in "${requested_skills[@]}"; do
    if [[ "$requested" == "$skill" ]]; then
      return 0
    fi
  done

  return 1
}

list_skills() {
  local src
  local skill
  local description

  for src in "$SKILLS_DIR"/*; do
    [[ -d "$src" ]] || continue
    [[ -f "$src/SKILL.md" ]] || continue
    skill="$(basename "$src")"
    description=""
    if [[ -f "$src/agents/openai.yaml" ]]; then
      description="$(awk -F'"' '/short_description:/ {print $2; exit}' "$src/agents/openai.yaml")"
    fi
    if [[ -z "$description" ]]; then
      description="$(awk -F': ' '/^description:/ {print $2; exit}' "$src/SKILL.md")"
    fi
    printf '%-22s %s\n' "$skill" "$description"
  done
}

missing_requested=()
if [[ "${#requested_skills[@]}" -gt 0 ]]; then
  for requested in "${requested_skills[@]}"; do
    if [[ ! -f "$SKILLS_DIR/$requested/SKILL.md" ]]; then
      missing_requested+=("$requested")
    fi
  done
fi

if [[ "${#missing_requested[@]}" -gt 0 ]]; then
  echo "Unknown skill(s): ${missing_requested[*]}" >&2
  echo >&2
  echo "Available skills:" >&2
  list_skills >&2
  exit 2
fi

if [[ "$MODE" == "list" ]]; then
  list_skills
  exit 0
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
  should_include_skill "$(basename "$src")" || continue

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
