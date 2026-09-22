---
name: stock-signal
description: A股短线交易纪律监控、Jev 分析与飞书/Slack 通知。用于盘中行情分析、买卖提醒、仓位、止损、交易计划和 Codex 自动化；不执行真实交易。
---
# stock-signal：A股买点/平仓提醒

## 路径解析（每次先确认）
- `SKILL_DIR` = 本技能目录（通常 `~/.codex/skills/stock-signal`）
- `DATA_DIR`（plan.json / .env / 状态文件所在）解析顺序：环境变量 `STOCK_DATA_DIR` → `SKILL_DIR/scripts/.data_dir` 文件内容 → `SKILL_DIR/scripts`
- 确认方式：`python3 -c "import sys; sys.path.insert(0,'SKILL_DIR/scripts'); import datadir; print(datadir.data_dir())"`（替换 SKILL_DIR 为实际路径）
- 所有命令：`python3 SKILL_DIR/scripts/monitor.py --mode ...`（无需 cd）

## Codex 自动化（默认）

每个有效的五分钟轮次由 `scripts/codex_cycle.py run` 取得最新行情、运行 Jev，并产出完整但可直接发送的报告。它只分析和生成提醒，**不调用旧 `monitor.py --mode intraday`，也不登记 paper 或真实买入**。

- 飞书：报告有待发的 `feishu` 通道时，运行 `send-feishu --id REPORT_ID`；成功后脚本会确认该通道。
- Slack：仅用 Codex Slack connector 发送。按用户提供的工作区及用户/频道确认目标，发送后先通过读取或搜索含 `report_id` 的消息回查；确认送达后运行 `ack --id REPORT_ID --channel slack --receipt RECEIPT`。失败或结果含糊时不要盲目重发。
- 状态保存在 `DATA_DIR/codex_monitor`，按通道独立记录送达，避免重复通知；没有新信号时保持安静。

创建或修改自动化前，读取 [Codex 自动化操作说明](references/codex-automation.md)。任务时间可能超过五分钟；脚本负责交易日和时段守卫，不能承诺实时送达。

## 组件
- `plan.json`（在 DATA_DIR）：标的、买卖规则、仓位、止损/兑现。用户买入后执行 `--mode buy` 登记
- `monitor.py --mode test|once|auction|intraday|close|buy|sell` 是旧兼容链路。`buy`/`sell` 仍用于人工登记；它们会按旧推送设置发送，不属于 Codex 自动化。
- `quotes.py` 腾讯免费行情（免key，含VWAP分时均价）；`push.py` 旧链路的 lark-cli/微信推送（.env 配 LARK_USER_OPEN_ID）
- `market.py` 东财免费情绪与板块（免key）：涨停/炸板/跌停池→涨停家数/最高连板/炸板率/连板梯队；行业+概念板块涨幅榜（clist限流自动切push2delay备用域名）；涨停板块分布（主线判定）；个股所属行业
- 每条推送自动附带：情绪面板 + 板块热度 + 仓位汇总（已用%/上限%，超限⚠️）+ 当日/次日买点价格区间
- `jev_analyze.py` TypeSafe Jev 结构化判断（.env 配 TYPESAFE_API_KEY）；Codex 轮次必须把其市场情绪、主线和每只观察股的动作与理由纳入报告，不写成无关的研究长文
- `execution.py` 执行层骨架（paper 模拟：触发买点即登记模拟委托到 trade_log.jsonl；三重熔断：单日亏-2%停机/单票日3次/白名单。真实下单需 miniQMT+显式开启，默认安全拒绝）
- `cron.template` 仅保留旧 Claude/cron 回滚入口。启用 Codex 自动化后不要并行运行它。

## 工作流
1. **快照**：`--mode once` 获取实时状态（含情绪面板/板块分布/个股行业/仓位/买点）
2. **"分析/该不该买/选股"**：运行 Codex cycle，基于最新行情与 Jev 结果输出市场情绪、主线和每只观察股的明确动作及理由；报告发送前检查待发通道
3. **改计划**：编辑 DATA_DIR/plan.json，改完 `--mode once` 验证
4. **"我买了X"**：`--mode buy --code X`（价格省略=自动取实时价登记，报了价则 `--price Y`）；清仓 `--mode sell --code X`
5. **测试旧推送**：`--mode test`（不会测试 Slack；Slack 需通过 Codex connector 实际送达并回查）
6. **"周一校准"**：读 DATA_DIR/monday_snapshot.txt（若存在）更新 Jev state 重跑

## 规则细节
- 低吸区 = MA5±1%（陈小群·主线龙头分歧低吸）
- 弱转强 = 竞价高开2-5% + 10:00后 现价≥开盘价×1.01 且 ≥VWAP（小鳄鱼·二板确认）
- 确认加仓（持仓时自动盯）：浮盈≥5% + 有量（当日量≥昨日五成）+ 非涨停（一致加速不加仓）→ 金字塔加0.5成；加仓后用 `--mode buy --code X --price 新均价 --size N` 更新登记
- 大盘退潮预警（持仓时自动盯）：炸板率≥30% 或 最高连板较前日降≥2 或 跌停≥20家 → 提醒全部持仓减半/清仓、停止新开仓
- 仓位：情绪周期定总仓上限；首测期试错仓0.5成、确认加仓0.5成、单票≤2成；shares_for() 按总资金换算A股整手
- 弱转强双分支：高开2-5%站稳VWAP 或 低开-2%~+2%回升翻红站稳VWAP（confirm.low_open）
- 加仓量比按时间归一：10:00需昨日量20% → 15:00需100%（vol_ratio_threshold）
- 板块联动过滤：所属行业当日0家涨停 → 提醒降级为'观望不下单'
- 可靠性：9:25心跳（收不到=系统挂了）/ plan.json损坏告警 / 推送重试 / cron含周六(调休) / 9:10 caffeinate防睡眠
- 消息面：竞价推送含隔夜外盘(纳指/恒生/A50) + 个股近1日公告扫描(减持/立案等利空关键词)

## 盘中巡逻协议（任何 agent 会话按此执行，每20-30分钟一轮）
1. 运行 `--mode once` + `market.sector_dist()` + `market.is_ebb()`
2. 判断四件事：①主线延续性/新主线聚集（当日基线对比，≥4家记录首板候选）②华天类暂停标的激活条件（半导体≥4家→提示恢复buy_enabled）③watchlist弱转强提前信号（逼近VWAP收复/接近低吸区）④情绪周期变化（is_ebb翻转）
3. 有可执行机会→推送飞书（标的/价位/0.5成仓位）+对话≤3行简报；无→一行"巡逻HH:MM 无新机会"
4. 午休(11:30-13:00)/收盘后跳过；**绝不因巡逻临时推翻既定规则**（弱转强阈值/退潮闸门/个股buy_enabled）
当前背景判断可从 plan.json 各标的 note/pause_reason 字段恢复，历史判断看 jev_result.json 与收盘推送

## 战法速查（写进每次分析）
- 陈小群：只做主线情绪龙头；买在分歧卖在一致；低位首板=新主线+大资金+低位；一致加速是兑现点
- 小鳄鱼：仓位管控优先于技术；首板难中、二板弱转强要确定性；弱势只做超跌+缩量
- 生态警示：量化挤压下散户打板接力性价比极低

## 纪律（每次回复末尾附带）
高潮期总仓≤3成、单票试错≤1成、止损无条件当日执行。技术研究辅助，不构成投资建议。
