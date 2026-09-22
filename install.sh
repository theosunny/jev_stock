#!/usr/bin/env bash
# Install without overwriting a user's trading data or credentials.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./install.sh [--target codex|claude] [--data-dir /absolute/path]

Install stock-signal for Codex (the default) or Claude. --data-dir keeps all
plan, credential, and runtime data outside the installed skill.
EOF
}

target="codex"
data_dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      [ "$#" -ge 2 ] || { echo "--target requires codex or claude" >&2; exit 2; }
      target="$2"; shift 2 ;;
    --data-dir)
      [ "$#" -ge 2 ] || { echo "--data-dir requires an absolute path" >&2; exit 2; }
      data_dir="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$target" in
  codex) dest_base="$HOME/.codex/skills" ;;
  claude) dest_base="$HOME/.claude/skills" ;;
  *) echo "--target must be codex or claude" >&2; exit 2 ;;
esac
if [ -n "$data_dir" ]; then
  case "$data_dir" in
    /*) ;;
    *) echo "--data-dir must be an absolute path" >&2; exit 2 ;;
  esac
fi

src="$(cd "$(dirname "$0")" && pwd)/stock-signal"
dest="$dest_base/stock-signal"
mkdir -p "$dest_base"
stage="$(mktemp -d "$dest_base/.stock-signal-stage.XXXXXX")"
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT

# Local files are overlaid in staging; they are never copied into this repo.
cp -R "$src/." "$stage"
if [ -d "$dest" ]; then
  for file in .env plan.json .alert_state.json monday_snapshot.txt jev_result.json trade_log.jsonl; do
    [ -f "$dest/$file" ] && cp -p "$dest/$file" "$stage/$file"
  done
  if [ -f "$dest/scripts/.data_dir" ]; then
    cp -p "$dest/scripts/.data_dir" "$stage/scripts/.data_dir"
  fi
fi
if [ -n "$data_dir" ]; then
  mkdir -p "$data_dir"
  printf '%s\n' "$data_dir" > "$stage/scripts/.data_dir"
fi
[ -f "$stage/.env" ] || cp "$stage/.env.example" "$stage/.env"

backup=""
if [ -d "$dest" ]; then
  backup="$(mktemp -d "$dest_base/stock-signal.backup.XXXXXX")"
  rmdir "$backup"
  mv "$dest" "$backup"
fi
mv "$stage" "$dest"
trap - EXIT

echo "Installed stock-signal for $target: $dest"
[ -n "$backup" ] && echo "Previous install backed up: $backup"
echo "No market request or monitor run was performed during installation."
echo "Configure $dest/.env, then create the Codex automation from $dest/references/codex-automation.md."
echo "Keep it PAUSED until both Feishu and Slack delivery have been verified."
