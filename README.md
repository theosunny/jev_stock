# jev_stock — A股买点/平仓提醒（Codex Skill）

陈小群/小鳄鱼游资战法规则化监控：**MA5低吸区 / VWAP弱转强确认 / 止损 / 涨停与冲高回落兑现**。Codex 每个有效五分钟轮次生成 Jev 完整判断，并分别推送到飞书和 Slack；不自动下单，也不登记 paper 委托。

## 个人交易助手界面

已新增独立本机界面，按已确认的四页原型实现大盘、滚动选股、个股行情/财报与推荐验证。使用现有研究归档，不改变盘中监控、交易计划或真实持仓。

```sh
STOCK_DATA_DIR=/Users/habitat/ai_project/stock_anaylze /Users/habitat/miniforge3/bin/python3 -m assistant.server
```

打开 [个人交易助手](http://127.0.0.1:8766)。界面定期读取现有归档；新正式推荐进入独立 SQLite 留档，历史不删除。独立模拟操作仅在验证页手动触发，国金证券适配边界默认关闭。收益回测尚未完成，页面不会展示虚构绩效。

设计、数据边界与验证说明见 [assistant/README.md](assistant/README.md)。不需要安装新的生产依赖。

## 快速开始（任何 Mac/Linux）

```bash
git clone https://github.com/theosunny/jev_stock.git
cd jev_stock && ./install.sh
# 编辑 ~/.codex/skills/stock-signal/.env 与 plan.json
```

需要保留 Claude 兼容安装时，执行 `./install.sh --target claude`。要将数据放在技能目录外，执行 `./install.sh --data-dir /absolute/path/to/stock-data`；随后编辑该外置目录的 `.env` 与 `plan.json`，安装器不会覆盖既有凭证。

然后在 Codex 里说 **「看下股票」** 即可触发。

## 依赖

| 依赖 | 用途 | 必须? |
|---|---|---|
| Python 3（仅标准库） | 全部脚本 | ✅ |
| [lark-cli](https://feishu.cn)（登录后 `lark-cli auth status` 查 openId） | 飞书推送 | 推送功能需要 |
| 微信通道（任选一）：Server酱 / PushPlus / 企业微信机器人 / WxPusher | 微信推送 | 可选，填 .env 即启用 |
| TypeSafe API Key（.env） | Codex 的完整 Jev 分析 | Codex 自动化需要；旧 monitor 规则提醒可不填 |
| 已安装到工作区的 Slack App Bot | Slack 推送 | Slack 通道需要；本机 .env 配 SLACK_BOT_TOKEN / SLACK_USER_ID，不提交真实值 |
| 腾讯行情 + 东财情绪/板块接口 | 数据源 | 免费，无需 key |

## 功能

- **情绪与板块面板**：涨停家数/最高连板/炸板率/连板梯队、行业+概念涨幅榜、涨停板块分布（主线判定）、个股所属行业——每条推送自动附带
- **仓位管理**：每条推送带仓位汇总（已用%/上限%），买入登记超限自动⚠️警告
- **Codex 盘中自动化**：每五分钟获取行情、运行 Jev，并发送市场情绪、主线及每只观察股的动作与理由；每通道单独确认，未确认不会盲目重发
- **可靠性保障**：9:25 强制心跳报告、plan 损坏告警、通道回查、9:10 `caffeinate` 本机防休眠辅助
- **消息面**：竞价含隔夜外盘（纳指/恒生/A50）与个股公告风险扫描
- **弱转强双分支**：高开站稳VWAP 或 低开回升翻红
- **执行层骨架（paper）**：旧兼容链路才会登记模拟委托；Codex 自动化只分析和通知，真实下单需 miniQMT+明确启用
- **确认加仓/大盘退潮预警**：持仓浮盈≥5%有量→金字塔加仓提醒（涨停日不加）；炸板率≥30%/高度板骤降→全组合减仓预警
- **买入/清仓登记**：`python3 <skill>/scripts/monitor.py --mode buy --code sz002185 --price 17.80`（自动算止损价并推送回执）
- **Jev 深度分析**：动态 `live_jev.py` 产出情绪周期、主线与标的动作判定；`jev_analyze.py` 仅保留旧版手动分析兼容入口
- **数据目录可移植**：默认在 skill 目录；设 `STOCK_DATA_DIR` 或写 `scripts/.data_dir` 可把数据放到任意位置

## 微信推送配置（任选其一，填 .env 即启用，可与飞书同时收）

| 通道 | 获取方式 | 费用/限制 | 消息在哪看 |
|---|---|---|---|
| **Server酱** | 微信扫码 https://sct.ftqq.com 拿 SendKey → `SERVERCHAN_SENDKEY=` | 免费5条/天，更多¥4.9/月 | 微信「Server酱」服务号 |
| **PushPlus** | 微信扫码 https://www.pushplus.plus 拿 token → `PUSHPLUS_TOKEN=` | 免费额度较大，偶尔限流 | 微信公众号 |
| **企业微信机器人** | 企业微信建群→群设置→机器人→复制webhook → `WECOM_WEBHOOK_URL=` | 免费、无条数限制 | 企业微信 App（可开通知） |
| **WxPusher** | https://wxpusher.zjiecode.com 注册应用 → `WXPUSHER_APP_TOKEN=` + `WXPUSHER_UID=` | 免费 | 微信公众号 |

填好后验证：`python3 scripts/monitor.py --mode test`（显示 `已推送(feishu(bot)+serverchan)` 即多通道生效）

## 运行模式：Hybrid Monitoring（推荐）

**核心理念：低成本定时脚本 + 按需 LLM 分析**

系统采用混合架构，将监控与通知分离为两层：
- **调度层**：轻量级 Python tick（`codex_tick.py`），每 5 分钟执行一次 `pending → verify → run → send` 完整周期，无需 LLM
- **分析层**：`codex_cycle.py` 在 `run` 阶段按需调用 TypeSafe Jev（需 TYPESAFE_API_KEY）；数据异常时保持持仓监控，新买入自动暂停

### 方式一：本机 watchdog（无 cron 环境）

```bash
# 数据目录与脚本路径已自动解析（datadir.py），也可显式设置
export STOCK_DATA_DIR=/path/to/stock-data  # 可选
nohup bash stock-signal/scripts/codex_watchdog.sh > /dev/null 2>&1 &
```

watchdog 循环将自动：
- 每 5 分钟唤醒 `codex_tick.py`
- 写入健康状态到 `$STOCK_DATA_DIR/logs/codex-health.json`
- 日志保存在 `$STOCK_DATA_DIR/logs/codex-tick.log` / `.err`
- watchdog 自身日志在 `codex-watchdog.log`

停止：`kill $(cat $STOCK_DATA_DIR/logs/codex-watchdog.pid)`

### 方式二：系统 cron（推荐）

```cron
# 交易日每 5 分钟
*/5 9-15 * * 1-6 cd ~/.codex/skills/stock-signal && /usr/bin/python3 scripts/codex_tick.py
```

`codex_tick.py` 内置交易日判断，休市日/非交易时段自动跳过。

### 健康监控

脚本每轮写入 `$STOCK_DATA_DIR/logs/codex-health.json`：

```json
{
  "ok": true,
  "at": "2024-03-15 10:05:03 CST",
  "run_status": "success",
  "report_id": "20240315-intraday-1005-abc123",
  "pending": {"20240315-intraday-1005-abc123": ["slack"]},
  "alerts": [],
  "unconfirmed": {}
}
```

- `ok`: `false` 时表示硬失败（run_error / delivery_failed / slack_unconfirmed）
- `alerts`: 触发的问题列表
- **注意**：`feishu_not_configured` 不会标记为 `ok=false`（lark-cli 可选）

### 可选：Codex Agent 健康巡检

创建**低频**（如 30 分钟）Codex 任务，读取 `codex-health.json`，仅在 `ok=false` 时通知：

```
每 30 分钟读取 STOCK_DATA_DIR/logs/codex-health.json。
ok=true → 静默；ok=false → 报告 alerts 并建议人工核查。
不执行任何交易或重发报告；tick 脚本已负责正常循环。
```

这样 LLM 成本降至最低，仅用于异常提醒。

## 旧链路：完整 LLM 调度（不推荐并行）

详细提示词和切换步骤见 [Codex 自动化说明](stock-signal/references/codex-automation.md)。先创建一个 **PAUSED** 的五分钟 Codex 任务，手动验证飞书和 Slack 都收到完整 Jev 报告并完成回查确认；随后备份并移除旧 `monitor.py` 和 `monday_nudge` cron 条目，再将 Codex 任务设为 **ACTIVE**。需要回滚时，暂停 Codex 并恢复已备份的监控条目。

**重要**：`codex_tick.py` / `codex_watchdog.sh` 与完整 Codex 自动化**不能并行运行**——它们都会调用同一个 `codex_cycle.py` 并争抢 claim/ack 锁。选择其一即可。

`stock-signal/cron.template` 的监控行只保留为旧链路回滚入口，不能与 Codex 自动化并行运行。9:10 `caffeinate` 可保留为本机 OS 防休眠辅助；它不是监控调度，且 Codex 不能唤醒休眠中的 Mac。模板包含周六是为兼容调休工作日；实际是否开市仍由脚本交易日判断，调休周六不是 A 股开市信号。

首次手动验证仅在竞价 9:15–9:30、盘中 9:30–11:30/13:00–15:00 或收盘 15:00–15:20 进行。报告超过 `expires_at` 后不发送，等待下一轮新报告。

## 交易计划（plan.json）

watchlist 每项含：策略类型（ma5低吸 / 弱转强确认）、买入参数、仓位、止损百分比、兑现规则；买入登记后 `ref_price` 生效，持仓监控自动激活。当天买入的 A 股受 T+1 限制，止损提示会标记为下一交易日优先处理，不能承诺当日卖出。

## 风险声明

本项目是交易纪律的规则化辅助与技术研究，**不构成投资建议**。打板/接力类策略散户胜率极低，情绪高潮期随时可能退潮，请自行承担交易风险。
