---
name: stock-signal
description: A股短线买点/平仓监控与飞书推送（陈小群/小鳄鱼游资战法 + TypeSafe Jev 结构化判断）。当用户提到股票、A股、买点、买入选股、卖点、平仓、止损、止盈、仓位、盯盘、复盘、涨停、连板、打板、低吸、弱转强、情绪周期、交易计划，或要求查看/修改/推送股票提醒、运行 monitor.py、更新 plan.json 时，务必使用本技能——即使用户没有点名 stock-signal 也要用。
---
# stock-signal：A股买点/平仓提醒

## 路径解析（每次先确认）
- `SKILL_DIR` = 本技能目录（通常 ~/.claude/skills/stock-signal）
- `DATA_DIR`（plan.json / .env / 状态文件所在）解析顺序：环境变量 `STOCK_DATA_DIR` → `SKILL_DIR/scripts/.data_dir` 文件内容 → `SKILL_DIR/scripts`
- 确认方式：`python3 -c "import sys; sys.path.insert(0,'SKILL_DIR/scripts'); import datadir; print(datadir.data_dir())"`（替换 SKILL_DIR 为实际路径）
- 所有命令：`python3 SKILL_DIR/scripts/monitor.py --mode ...`（无需 cd）

## 组件
- `plan.json`（在 DATA_DIR）：标的、买卖规则、仓位、止损/兑现。用户买入后执行 `--mode buy` 登记
- `monitor.py --mode test|once|auction|intraday|close|buy|sell`
  - buy: `--code sz002185 --price 17.80`（写 ref_price、算止损、推送回执）；sell: `--code sz002185`
- `quotes.py` 腾讯免费行情（免key，含VWAP分时均价）；`push.py` lark-cli 飞书推送（.env 配 LARK_USER_OPEN_ID）
- `market.py` 东财免费情绪与板块（免key）：涨停/炸板/跌停池→涨停家数/最高连板/炸板率/连板梯队；行业+概念板块涨幅榜（clist限流自动切push2delay备用域名）；涨停板块分布（主线判定）；个股所属行业
- 每条推送自动附带：情绪面板 + 板块热度 + 仓位汇总（已用%/上限%，超限⚠️）+ 当日/次日买点价格区间
- `jev_analyze.py` TypeSafe Jev 深度判断（.env 配 TYPESAFE_API_KEY，结果写 DATA_DIR/jev_result.json）
- cron 定时建议：交易日 9:16 auction / 每5分钟 intraday / 15:12 close（见仓库 cron.template）

## 工作流
1. **快照**：`--mode once` 获取实时状态（含情绪面板/板块分布/个股行业/仓位/买点）
2. **"分析/该不该买/选股"**：用最新行情与复盘更新 `jev_analyze.py` 的 state（市场状态/连板梯队/主线），运行之，读 jev_result.json，结合战法解读
3. **改计划**：编辑 DATA_DIR/plan.json，改完 `--mode once` 验证
4. **"我买入了X@价格Y"**：`--mode buy --code X --price Y`；清仓 `--mode sell --code X`
5. **测试推送**：`--mode test`
6. **"周一校准"**：读 DATA_DIR/monday_snapshot.txt（若存在）更新 Jev state 重跑

## 规则细节
- 低吸区 = MA5±1%（陈小群·主线龙头分歧低吸）
- 弱转强 = 竞价高开2-5% + 10:00后 现价≥开盘价×1.01 且 ≥VWAP（小鳄鱼·二板确认）
- 确认加仓（持仓时自动盯）：浮盈≥5% + 有量（当日量≥昨日五成）+ 非涨停（一致加速不加仓）→ 金字塔加0.5成；加仓后用 `--mode buy --code X --price 新均价 --size N` 更新登记
- 大盘退潮预警（持仓时自动盯）：炸板率≥30% 或 最高连板较前日降≥2 或 跌停≥20家 → 提醒全部持仓减半/清仓、停止新开仓
- 仓位：情绪周期定总仓上限；首测期试错仓0.5成、确认加仓0.5成、单票≤2成；shares_for() 按总资金换算A股整手

## 战法速查（写进每次分析）
- 陈小群：只做主线情绪龙头；买在分歧卖在一致；低位首板=新主线+大资金+低位；一致加速是兑现点
- 小鳄鱼：仓位管控优先于技术；首板难中、二板弱转强要确定性；弱势只做超跌+缩量
- 生态警示：量化挤压下散户打板接力性价比极低

## 纪律（每次回复末尾附带）
高潮期总仓≤3成、单票试错≤1成、止损无条件当日执行。技术研究辅助，不构成投资建议。
