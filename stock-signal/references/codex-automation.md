# Codex 五分钟自动化

安装后，先确认 `SKILL_DIR` 与 `DATA_DIR`，并将任务设为 **PAUSED**。自动化提示应要求 Codex 执行：

```text
运行 python3 <SKILL_DIR>/scripts/codex_cycle.py run。
若返回 pending_channels 含 feishu，执行 send-feishu --id <report_id>。
若含 slack，通过已连接的 Slack connector 将完整 message_path 内容发送给已确认的目标；读取或搜索 report_id 回查成功后，执行 ack --id <report_id> --channel slack --receipt <Slack 回执>。
不要调用 monitor.py --mode intraday、不要执行或登记任何买卖；无待发报告时保持安静。
```

将任务设为每 5 分钟运行。脚本会跳过非交易时段、休市日和无变化轮次；运行时间可能超过 5 分钟，因此调度频率不是实时保证。Slack 目标属于运行时私有配置：按用户提供的工作区和用户或频道确认，勿写入仓库或 `.env.example`。

切换按以下顺序进行：

1. 保持 Codex 任务 **PAUSED**，手动执行一轮，分别验证飞书和 Slack 的完整报告与回执确认。
2. 备份旧 stock-signal cron 条目，再移除它们，防止旧 `intraday` 和 Codex cycle 重复提醒或重复登记 paper 委托。
3. 将 Codex 任务改为 **ACTIVE**。
4. 回滚时暂停 Codex 任务，恢复已备份的 cron 条目。

`send-feishu` 或 Slack 发送失败时保留待发状态并先回查，不重发不明状态的报告。该链路仅提供技术研究与提醒，不构成投资建议。
