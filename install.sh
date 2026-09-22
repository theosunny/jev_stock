#!/usr/bin/env bash
# Install without overwriting a user's trading data or credentials.
set -euo pipefail
umask 077

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
chmod 700 "$stage"
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT

# Local files are overlaid in staging; they are never copied into this repo.
cp -R "$src/." "$stage"
find "$stage" \( -type d -name __pycache__ -o -type d -name .pytest_cache \) -prune -exec rm -rf {} +
find "$stage" -type f \( -name '*.pyc' -o -name '.coverage' -o -name coverage.xml \) -delete
if [ -d "$dest" ]; then
  for file in .env plan.json .alert_state.json monday_snapshot.txt jev_result.json trade_log.jsonl; do
    [ -f "$dest/$file" ] && cp -p "$dest/$file" "$stage/$file"
  done
  if [ -f "$dest/scripts/.data_dir" ]; then
    cp -p "$dest/scripts/.data_dir" "$stage/scripts/.data_dir"
  fi
  for directory in codex_monitor reviews; do
    [ -d "$dest/$directory" ] && cp -a "$dest/$directory" "$stage/$directory"
  done
  for file in .env plan.json; do
    [ -f "$dest/scripts/$file" ] && cp -p "$dest/scripts/$file" "$stage/scripts/$file"
  done
fi
if [ -n "$data_dir" ]; then
  mkdir -p "$data_dir"
  [ -f "$data_dir/plan.json" ] || cp "$stage/plan.json" "$data_dir/plan.json"
  [ -f "$data_dir/.env" ] || cp "$stage/.env.example" "$data_dir/.env"
  chmod 600 "$data_dir/plan.json" "$data_dir/.env"
  printf '%s\n' "$data_dir" > "$stage/scripts/.data_dir"
fi
[ -f "$stage/.env" ] || cp "$stage/.env.example" "$stage/.env"
for file in .env plan.json .alert_state.json monday_snapshot.txt jev_result.json trade_log.jsonl; do
  [ -f "$stage/$file" ] && chmod 600 "$stage/$file"
done
for file in .env plan.json .data_dir; do
  [ -f "$stage/scripts/$file" ] && chmod 600 "$stage/scripts/$file"
done
if [ -d "$stage/codex_monitor" ]; then
  find "$stage/codex_monitor" -type d -exec chmod 700 {} +
  find "$stage/codex_monitor" -type f -exec chmod 600 {} +
fi

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
if [ -n "$data_dir" ]; then
  echo "Configure $data_dir/.env and $data_dir/plan.json."
else
  echo "Configure $dest/.env and $dest/plan.json."
fi
echo "Then create the Codex automation from $dest/references/codex-automation.md."
echo "Keep it PAUSED until both Feishu and Slack delivery have been verified."
