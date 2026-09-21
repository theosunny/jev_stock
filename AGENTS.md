# AGENTS.md — jev_stock 开发规范

A 股短线买点/平仓监控 + 飞书/微信推送的 Claude Code Skill（陈小群/小鳄鱼战法规则化 + TypeSafe Jev 判断）。

## 结构与职责

```
stock-signal/
├── SKILL.md            # Claude 触发与工作流（用户侧文档，改动需同步本机安装）
├── plan.json           # 交易计划模板（标的/规则/仓位/execution熔断配置）
├── .env.example        # TYPESAFE_API_KEY / LARK_USER_OPEN_ID / 微信四通道
├── cron.template       # 定时任务模板（1-6含调休周六）
└── scripts/
    ├── datadir.py      # 数据目录解析: $STOCK_DATA_DIR → scripts/.data_dir → SKILL.md所在根目录
    ├── quotes.py       # 腾讯免费行情（实时+日K+VWAP分时均价+昨量）
    ├── market.py       # 东财免费：涨停/炸板/跌停池(情绪)、板块榜(clist限流自动切push2delay)、
    │                   #   板块涨停分布(主线判定)、个股行业、公告扫描、隔夜外盘、is_ebb退潮判定
    ├── monitor.py      # 规则引擎: test/once/auction/intraday/close/buy/sell/heartbeat
    ├── push.py         # 多通道: feishu(lark-cli bot优先) + Server酱/PushPlus/企微webhook/WxPusher
    ├── execution.py    # paper模拟执行层 + 三重熔断(日亏2%/单票3次/白名单)；真实下单需miniQMT
    └── jev_analyze.py  # TypeSafe Jev 结构化判断（state内嵌，随行情更新）
```

## 开发流程（必须遵守）

1. **只改仓库**（`~/ai_project/jev_stock`），本机运行副本 `~/.claude/skills/stock-signal` 靠 cp 同步，数据目录靠 `.data_dir` 指向 `~/ai_project/stock_anaylze`
2. 测试：`STOCK_DATA_DIR=~/ai_project/stock_anaylze NO_PUSH=1 python3 scripts/monitor.py --mode once`
   - `NO_PUSH=1` 静默不推送；`--force` 忽略时段/交易日限制
3. 同步本机：`cp 改动文件 ~/.claude/skills/stock-signal/scripts/ && rm -rf .../scripts/__pycache__`（**别删 .data_dir**）
4. git 提交推送；**盘中（9:30-15:00）不动核心链路，等收盘**

## 血泪坑（全部真实踩过，别再踩）

1. **emoji 必须用八位转义** `\U0001F525`；`🔥` 代理对在 print 时直接 UnicodeEncodeError 崩溃
2. **补丁字符串里的 `\n`**：heredoc+JSON 多层转义会把字面 `\n` 变真换行，构造时用 `chr(92)+"n"`；块替换用锚点函数（start/end 定位），零碎行匹配必失败
3. **cron 最小 PATH**：lark-cli 是 `#!/usr/bin/env node`，cron 里找不到 node → push.py 已自动注入 bin 目录；**验证必须用** `env -i HOME=... PATH=/usr/bin:/bin /bin/sh -c '...'` 模拟，终端测试会掩盖此问题
4. **开盘首小时（9:30-10:00）涨停池是小样本噪音**：炸板率在 29 家分母上可虚高 10 个点（9:45 判退潮、10:05 反转的教训）——周期判定强制用昨日完整数据（market_lines 与 is_ebb 均已内置）
5. **板块聚集扫描必须对比当日基线**，相邻 5 分钟 diff 永远抓不到"每 10 分钟 +1 家"的稳步聚集（医药 3→15 家全程静默的教训）
6. **成交价只有用户知道**：买入登记 `--mode buy --code X` 免价格（取实时价）；成交回报类全自动需 miniQMT
7. 涨停日不加仓（一致加速=兑现点）；一字板买不进也不该买——规则自洽处不要"优化"掉

## 战法与纪律（写进每次分析回复）

- 陈小群：只做主线情绪龙头；买在分歧卖在一致；低位首板=新主线+大资金+低位；主线轮出时暂停个股买入（buy_enabled=false + pause_reason）
- 小鳄鱼：仓位管控优先于技术；首板难中、次日弱转强(高开2-5%或低开回升，站稳VWAP)要确定性
- 退潮闸门（is_ebb）：炸板率≥25%/高度板降≥2/跌停≥10 → 全部买入类提醒自动暂停，持仓监控不受影响
- 首测纪律：总资金10万、试错仓0.5成、单票≤2成、止损-6%当日执行
- 每次回复末尾：技术研究辅助，不构成投资建议

## 重新入场信号（退潮后）

涨停家数回升 + 炸板率回落 + 某板块连续 2 日涨停 ≥4 家（新主线确认）
