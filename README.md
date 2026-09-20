# jev_stock — A股买点/平仓飞书提醒（Claude Code Skill）

陈小群/小鳄鱼游资战法规则化监控：**MA5低吸区 / VWAP弱转强确认 / 止损 / 涨停与冲高回落兑现**，触发即推送到飞书私聊；内置 TypeSafe Jev 结构化判断做深度分析。

## 快速开始（任何 Mac/Linux）

```bash
git clone https://github.com/theosunny/jev_stock.git
cd jev_stock && ./install.sh
# 编辑 ~/.claude/skills/stock-signal/.env 填入配置
```

然后在 Claude Code 里说 **「看下股票」** 即可触发。

## 依赖

| 依赖 | 用途 | 必须? |
|---|---|---|
| Python 3（仅标准库） | 全部脚本 | ✅ |
| [lark-cli](https://feishu.cn)（登录后 `lark-cli auth status` 查 openId） | 飞书推送 | 推送功能需要 |
| TypeSafe API Key（.env） | Jev 深度判断 | 可选，规则监控不依赖 |
| 腾讯行情接口 | 数据源 | 免费，无需 key |

## 功能

- **盘中自动监控**（配 cron，见 `stock-signal/cron.template`）：竞价概览 → 每5分钟规则检查（每票每类每天最多提醒1次）→ 收盘总结
- **买入/清仓登记**：`python3 <skill>/scripts/monitor.py --mode buy --code sz002185 --price 17.80`（自动算止损价并推送回执）
- **Claude 深度分析**：情绪周期定位、标的买点判定（`jev_analyze.py`，choice/noul/score 三类结构化问题）
- **数据目录可移植**：默认在 skill 目录；设 `STOCK_DATA_DIR` 或写 `scripts/.data_dir` 可把数据放到任意位置

## 交易计划（plan.json）

watchlist 每项含：策略类型（ma5低吸 / 弱转强确认）、买入参数、仓位、止损百分比、兑现规则；买入登记后 `ref_price` 生效，持仓监控自动激活。

## 风险声明

本项目是交易纪律的规则化辅助与技术研究，**不构成投资建议**。打板/接力类策略散户胜率极低，情绪高潮期随时可能退潮，请自行承担交易风险。
