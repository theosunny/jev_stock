"""Read-only strategy catalog for the personal stock assistant.

The catalog describes research frameworks for display.  It is deliberately
separate from plan files, risk configuration, orders, and any execution path.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SCHEMA_VERSION = 1
REQUIRED_FIELDS = (
    "id", "version", "name", "status", "summary", "horizon", "principles",
    "selection", "entry", "exit", "risk", "data_requirements", "validation",
)
LIST_FIELDS = (
    "principles", "selection", "entry", "exit", "risk", "data_requirements", "validation",
)
STATUSES = {"current", "template"}


class StrategyCatalogError(ValueError):
    """Raised when a bundled strategy catalog no longer matches its schema."""


_CATALOG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "current_strategy_id": "chen-xiaoq-un-rule-based-v1",
    "strategies": [
        {
            "id": "chen-xiaoq-un-rule-based-v1",
            "version": "1.0",
            "name": "陈小群思路 · 主线情绪短线",
            "status": "current",
            "summary": "以主线核心、分歧参与和仓位纪律为研究框架，结合既有小鳄鱼式风险约束；并非对任何个人战法的完整复刻。",
            "horizon": "短线至波段，按当日市场情绪和交易计划的有效期核验。",
            "principles": [
                "只研究可核验的主线核心与情绪龙头，不因龙头难参与而自动选择跟风。",
                "关注分歧承接，避免把一致加速当作默认加仓点。",
                "研究结论和正式买入候选分离；候选仍须通过当轮全部风控闸门。",
            ],
            "selection": [
                "滚动比较涨停、炸板、连板高度、板块聚集、公告催化和前轮候选承接。",
                "要求主线匹配与核心地位存在可核验证据；数据缺口只保留研究状态。",
                "排板或回封只在主线核心、行情与个股闸门同时满足时进入正式候选。",
            ],
            "entry": [
                "低位首板需同时具备新主线、大资金和低位等可核验条件。",
                "弱转强的仓库默认阈值：竞价高开2–5%，10:00后现价≥开盘价×1.01且≥VWAP。每只股票可由最新计划覆盖开盘范围、确认时间和涨幅参数；此处不是实时参数。低开回升分支仍需计划启用与完整确认。",
                "排板/回封需Jev明确确认主线核心、允许的情绪阶段、连续有效行情与买一证据；高潮/退潮不晋升。",
                "以上为本系统规则化阈值，并非陈小群本人固定参数。",
                "排板、回封候选不保证成交；五分钟采样不能替代实时打板系统。",
            ],
            "exit": [
                "一致加速或涨停日不默认加仓，结合计划与持仓风险提示处理。",
                "止损和退出仅按交易计划与硬风控触发；助手不自动下单或登记真实持仓。",
            ],
            "risk": [
                "退潮闸门：炸板率≥25%、高度较昨日下降≥2板、跌停≥10家任一触发时暂停买入，持仓风险监控继续；开盘早段按既有昨日完整样本规则。",
                "当日新买入受 T+1 限制，止损提醒需核对实际成本与可卖数量；不能保证当日卖出。",
                "首测纪律以总资金十万元、试错仓 0.5 成、单票上限 2 成、-6% 触发止损提醒为研究参考，实际以计划为准。",
                "市场、仓位、个股、恢复四类闸门任一未通过，均不展示正式买入建议。",
            ],
            "data_requirements": [
                "完整涨停/炸板池、昨日基线、连板标签、板块分布和最新行情时间。",
                "主线和核心地位证据、公告催化及缺口说明。",
                "Jev 结构化复核及当前交易计划的可用性状态。",
            ],
            "validation": [
                "正式推荐事件不可修改，并独立跟踪推荐后的可得价格观察。",
                "模拟成交必须满足可验证的时段、价格、成交量、涨跌停和 T+1 条件；排板/回封不会因粗粒度行情被假定成交。",
                "当前仅有归档样本核查与近 30 日覆盖统计；收益回测尚未完成，不展示胜率、收益或成交承诺。",
            ],
        },
        {
            "id": "buffett-value-research-template-v1",
            "version": "0.1",
            "name": "价值研究模板（巴菲特视角）",
            "status": "template",
            "summary": "面向长期价值研究的扩展模板，关注护城河、财务质量、估值与安全边际；尚未定义参数或接入执行。",
            "horizon": "长期研究，拟以多年经营与估值周期核验。",
            "principles": [
                "优先理解商业模式、竞争优势和资本配置，而非仅依据短期价格波动。",
                "将安全边际作为估值研究的前提，并明确无法量化的判断部分。",
            ],
            "selection": [
                "拟研究持续盈利能力、自由现金流、资本回报、负债质量与竞争壁垒。",
                "拟建立行业可比和估值区间，但尚未确定口径、阈值和数据源。",
            ],
            "entry": [
                "未定义。后续需由用户确认估值方法、买入节奏和分批规则后才可研究为可执行规则。",
            ],
            "exit": [
                "未定义。后续需定义基本面恶化、估值偏离与再平衡规则。",
            ],
            "risk": [
                "未启用，不参与当前选股、买入建议、模拟成交或风险闸门。",
                "长期价值判断含定性假设，不能以模板名称替代财务与业务核验。",
            ],
            "data_requirements": [
                "多年财务报表、现金流、估值数据、行业竞争资料和可追溯的公告/年报来源。",
                "当前助手仅展示部分财务面板，尚未具备该模板所需完整数据覆盖。",
            ],
            "validation": [
                "未实现收益回测、参数寻优或模拟执行。",
                "启用前需要独立定义避免前视偏差的历史样本、复权口径、交易成本和再平衡日。",
            ],
        },
    ],
}


def validate_catalog(catalog: dict[str, Any]) -> None:
    """Validate the small public schema without reading user configuration."""
    if not isinstance(catalog, dict) or catalog.get("schema_version") != SCHEMA_VERSION:
        raise StrategyCatalogError("unsupported strategy catalog schema")
    strategies = catalog.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise StrategyCatalogError("strategy catalog must contain strategies")

    strategy_ids: set[str] = set()
    for strategy in strategies:
        if not isinstance(strategy, dict) or any(field not in strategy for field in REQUIRED_FIELDS):
            raise StrategyCatalogError("strategy has missing required fields")
        strategy_id = strategy["id"]
        if not isinstance(strategy_id, str) or not strategy_id or strategy_id in strategy_ids:
            raise StrategyCatalogError("strategy identifiers must be unique")
        strategy_ids.add(strategy_id)
        if not isinstance(strategy["version"], str) or not strategy["version"]:
            raise StrategyCatalogError("strategy version is required")
        if strategy["status"] not in STATUSES:
            raise StrategyCatalogError("invalid strategy status")
        if any(not isinstance(strategy[field], str) or not strategy[field] for field in ("name", "summary", "horizon")):
            raise StrategyCatalogError("strategy text fields are required")
        if any(
            not isinstance(strategy[field], list)
            or not strategy[field]
            or any(not isinstance(item, str) or not item for item in strategy[field])
            for field in LIST_FIELDS
        ):
            raise StrategyCatalogError("strategy list fields must contain text")

    current = catalog.get("current_strategy_id")
    if current not in strategy_ids:
        raise StrategyCatalogError("current strategy must exist")
    current_strategy = next(item for item in strategies if item["id"] == current)
    if current_strategy["status"] != "current":
        raise StrategyCatalogError("current strategy must have current status")


validate_catalog(_CATALOG)


def get_catalog() -> dict[str, Any]:
    """Return a JSON-safe independent copy of the display-only catalog."""
    return deepcopy(_CATALOG)
