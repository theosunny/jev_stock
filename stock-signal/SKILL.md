---
name: stock-signal
description: A股短线交易纪律监控、Jev 分析与飞书/Slack 通知。用于盘中行情分析、买卖提醒、仓位、止损、交易计划和 Codex 自动化；不执行真实交易。
---
# stock-signal：A股买点/平仓提醒

## 路径解析（每次先确认）
- `SKILL_DIR` = 本技能目录（通常 `~/.codex/skills/stock-signal`）
- `DATA_DIR`（plan.json / .env / 状态文件所在）解析顺序：环境变量 `STOCK_DATA_DIR` → `SKILL_DIR/scripts/.data_dir` 文件内容 → 技能根目录（含 `SKILL.md` 的便携安装）→ `SKILL_DIR/scripts`
- 确认方式：`python3 -c "import sys; sys.path.insert(0,'SKILL_DIR/scripts'); import datadir; print(datadir.data_dir())"`（替换 SKILL_DIR 为实际路径）
- 所有命令：`python3 SKILL_DIR/scripts/monitor.py --mode ...`（无需 cd）

## Codex 自动化（默认）

每个有效的五分钟轮次由 `scripts/codex_cycle.py run` 取得最新行情、调用动态 `live_jev.py`，并产出完整但可直接发送的报告。它需要 `TYPESAFE_API_KEY`；未配置或 Jev 失败时新买入保持暂停。它只分析和生成提醒，**不调用旧 `monitor.py --mode intraday`，也不登记 paper 或真实买入**。

- 飞书：报告有待发的 `feishu` 通道时，运行 `send-feishu --id REPORT_ID`；成功后脚本会确认该通道。不明结果保留 `sending` 并返回 `delivery_uncertain`，等待人工核查。
- Slack：默认使用独立 Slack App Bot（本机 `.env` 的 `SLACK_BOT_TOKEN` / `SLACK_USER_ID`），运行 `send-slack --id REPORT_ID`；脚本建立机器人专属私信、记录目标、先 claim 后发送并确认。发送身份不是用户本人，手机通知仍受 Slack/系统设置影响。
- 每轮先处理 `pending`：Slack Bot 不明送达运行 `verify-slack --id REPORT_ID`；已找到机器人发送的编号则自动确认。`verified_absent` 仅代表完整回查未找到，至少隔一轮再次回查一致后才 `release`，不能确认就保留阻塞。返回 `legacy_connector_verification_required` 的旧报告才使用旧 connector 目标回查；不得向旧的自发私信发送新报告。飞书只在飞书目标回查，绝不混用回执。
- 状态保存在 `DATA_DIR/codex_monitor`，按通道独立记录送达，避免重复通知；没有新信号时保持安静。

创建或修改自动化前，读取 [Codex 自动化操作说明](references/codex-automation.md)。任务时间可能超过五分钟；脚本负责交易日和时段守卫，不能承诺实时送达。

## 组件
- `plan.json`（在 DATA_DIR）：标的、买卖规则、仓位、止损/兑现。用户买入后执行 `--mode buy` 登记
- `monitor.py --mode test|once|auction|intraday|close|buy|sell` 是旧兼容链路。`buy`/`sell` 仍用于人工登记；它们会按旧推送设置发送，不属于 Codex 自动化。
- `quotes.py` 腾讯免费行情（免key，含VWAP分时均价）；`push.py` 旧链路的 lark-cli/微信推送（.env 配 LARK_USER_OPEN_ID）
- `market.py` 东财免费情绪与板块（免key）：涨停/炸板/跌停池→涨停家数/最高连板/炸板率/连板梯队；行业+概念板块涨幅榜（clist限流自动切push2delay备用域名）；涨停板块分布（主线判定）；个股所属行业
- 每条推送自动附带：情绪面板 + 板块热度 + 仓位汇总（已用%/上限%，超限⚠️）+ 当日/次日买点价格区间
- `live_jev.py` 是 Codex 轮次的动态 Jev 结构化判断（需 TypeSafe Key）；报告必须含市场情绪、主线和每只观察股的动作与理由，不写成无关的研究长文。`jev_analyze.py` 仅保留为旧版手动分析兼容入口。
- `execution.py` 及自动 paper 委托只属于旧兼容链路；真实下单需 miniQMT+显式开启，默认安全拒绝。
- `cron.template` 仅保留旧 Claude/cron 回滚入口。启用 Codex 自动化后不要并行运行它。

## 工作流
1. **快照**：`--mode once` 获取实时状态（含情绪面板/板块分布/个股行业/仓位/买点）
2. **"分析/该不该买/选股"**：运行 Codex cycle，基于最新行情与 Jev 结果输出市场情绪、主线和每只观察股的明确动作及理由；报告发送前检查待发通道
3. **改计划**：编辑 DATA_DIR/plan.json，改完 `--mode once` 验证
4. **"我买了X"**：`--mode buy --code X`（价格省略=自动取实时价登记，报了价则 `--price Y`）；清仓 `--mode sell --code X`
5. **测试旧推送**：`--mode test`（不会测试 Slack；Slack 需通过独立 Bot 实际送达并回查）
6. **"周一校准"**：读 DATA_DIR/monday_snapshot.txt（若存在）更新 Jev state 重跑

## 规则细节
- 低吸区 = MA5±1%（陈小群·主线龙头分歧低吸）
- 弱转强 = 竞价高开2-5% + 10:00后 现价≥开盘价×1.01 且 ≥VWAP（小鳄鱼·二板确认）
- 确认加仓与退潮减仓提醒只属于旧兼容 `monitor.py` 链路；当前 Codex cycle 对已登记持仓只做止损/兑现监控，**不提供自动加仓建议**。
- 仓位：情绪周期定总仓上限；首测期试错仓0.5成、确认加仓0.5成、单票≤2成；shares_for() 按总资金换算A股整手
- 弱转强双分支：高开2-5%站稳VWAP 或 低开-2%~+2%回升翻红站稳VWAP（confirm.low_open）
- 加仓量比按时间归一：10:00需昨日量20% → 15:00需100%（vol_ratio_threshold）
- 板块联动过滤：所属行业当日0家涨停 → 提醒降级为'观望不下单'
- 可靠性：9:25普通 `run` 强制心跳报告（未收到时需检查运行与送达状态）/ plan.json损坏告警 / 通道回查 / 9:10 `caffeinate` OS 防休眠辅助。防休眠辅助不属于监控调度，Codex 无法唤醒休眠中的 Mac。
- 消息面：竞价推送含隔夜外盘(纳指/恒生/A50) + 个股近1日公告扫描(减持/立案等利空关键词)

## 盘中巡逻协议（可选人工会话，每20-30分钟一轮）
Codex 五分钟自动化是唯一的定时主流程。人工巡逻只能在用户要求时执行，不能另建并行调度或直接绕过通道确认。

1. 运行 `--mode once` + `market.sector_dist()` + `market.is_ebb()`
2. 判断四件事：①主线延续性/新主线聚集（当日基线对比，≥4家记录首板候选）②华天类暂停标的激活条件（半导体≥4家→提示恢复buy_enabled）③watchlist弱转强提前信号（逼近VWAP收复/接近低吸区）④情绪周期变化（is_ebb翻转）
3. 有可执行机会→通过当前 cycle 报告的待发通道发送（标的/价位/0.5成仓位）+对话≤3行简报；无→一行"巡逻HH:MM 无新机会"
4. 午休(11:30-13:00)/收盘后跳过；**绝不因巡逻临时推翻既定规则**（弱转强阈值/退潮闸门/个股buy_enabled）
当前背景判断可从 plan.json 各标的 note/pause_reason 字段恢复，历史判断看 jev_result.json 与收盘推送

## 战法速查（写进每次分析）
- 陈小群：只做主线情绪龙头；买在分歧卖在一致；低位首板=新主线+大资金+低位；一致加速是兑现点
- 小鳄鱼：仓位管控优先于技术；首板难中、二板弱转强要确定性；弱势只做超跌+缩量
- 生态警示：量化挤压下散户打板接力性价比极低

## 纪律（每次回复末尾附带）
高潮期总仓≤3成、单票试错≤1成；止损触发时按可卖数量执行，当日买入受 T+1 限制的仓位须标记为下一交易日优先处理。技术研究辅助，不构成投资建议。
