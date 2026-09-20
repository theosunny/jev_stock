#!/usr/bin/env bash
# 安装 stock-signal 到 ~/.claude/skills/（可重复执行，保留已有数据）
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/stock-signal"
DEST="$HOME/.claude/skills/stock-signal"
mkdir -p "$HOME/.claude/skills"
if [ -d "$DEST" ]; then
  BAK="$DEST.bak-$(date +%Y%m%d%H%M%S)"
  cp -R "$DEST" "$BAK"
  echo "已备份现有安装 -> $BAK"
  for f in .env plan.json .alert_state.json .data_dir monday_snapshot.txt jev_result.json; do
    [ -f "$DEST/$f" ] && cp "$DEST/$f" "$SRC/scripts/" 2>/dev/null || true
    [ -f "$DEST/$f" ] && cp "$DEST/$f" "$SRC/" 2>/dev/null || true
  done
fi
rm -rf "$DEST" && cp -R "$SRC" "$DEST"
cd "$DEST"
[ -f .env ] || cp .env.example .env
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }
python3 scripts/monitor.py --mode once || echo "（快照失败：请检查网络或 plan.json）"
echo ""
echo "安装完成: $DEST"
echo "后续步骤:"
echo "  1. 编辑 $DEST/.env 填入 LARK_USER_OPEN_ID（飞书推送，需安装 lark-cli 并 auth login）/ TYPESAFE_API_KEY（可选）"
echo "  2. 重启 Claude Code 会话，说「看下股票」验证技能触发"
echo "  3. 想要盘中自动提醒：参照 $DEST/cron.template 配置 crontab"
