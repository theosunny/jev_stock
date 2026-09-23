"""Read-only, allowlisted archive adapter for the personal stock assistant."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from assistant.provenance import UNKNOWN_STRATEGY, compare_snapshots


MAX_ARCHIVES = 1_000
MAX_CANDIDATES = 200
MAX_HISTORY = 100
CURRENT_CYCLE_SKEW_SECONDS = 10 * 60
FORMAL_ACTION = "买入候选"
MARKET_PHASES = {"回暖", "发酵", "高潮", "分歧", "退潮", "数据不足"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _read_archives(folder: Path, pattern: str, label: str, limit: int | None = MAX_ARCHIVES) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not folder.is_dir():
        return records, warnings
    paths = sorted(folder.glob(pattern))
    if limit is not None and len(paths) > limit:
        warnings.append(f"{label}归档数量超过上限；仅读取最新 {limit} 份")
        paths = paths[-limit:]
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("root is not an object")
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            warnings.append(f"无法读取{label}归档：{path.name}")
            continue
        records.append({"_path": path.name, "_data": value})
    return records, warnings


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strategy(value: Any) -> dict[str, Any]:
    """Expose only an archive's explicit complete strategy identity.

    The display catalog is intentionally not consulted here: it can change over
    time and must never be used to relabel historical research.
    """
    item = _as_dict(value)
    strategy_id, version, name = _text(item.get("id")), _text(item.get("version")), _text(item.get("name"))
    if not (strategy_id and version and name):
        return dict(UNKNOWN_STRATEGY)
    return {"id": strategy_id, "version": version, "name": name, "label": f"{name} · {version}", "known": True}


def _candidate_strategy(value: dict[str, Any], archive_strategy: dict[str, Any]) -> dict[str, Any]:
    explicit = _strategy(value.get("strategy"))
    return explicit if explicit["known"] else archive_strategy


def _snapshot_strategy(values: list[dict[str, Any]], archive_strategy: dict[str, Any]) -> dict[str, Any]:
    """Return a context identity only when all candidate evidence agrees.

    A mixed rolling pool can legitimately contain research from several
    frameworks.  Calling it a whole-pool strategy change would be misleading.
    """
    effective = [_candidate_strategy(value, archive_strategy) for value in values]
    known = [item for item in effective if item["known"]]
    if archive_strategy["known"]:
        if all((not item["known"]) or (item["id"], item["version"]) == (archive_strategy["id"], archive_strategy["version"]) for item in effective):
            return archive_strategy
        return dict(UNKNOWN_STRATEGY)
    if not effective or len(known) != len(effective):
        return dict(UNKNOWN_STRATEGY)
    first = known[0]
    return first if all((item["id"], item["version"]) == (first["id"], first["version"]) for item in known) else dict(UNKNOWN_STRATEGY)


def _record_time(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    data = _as_dict(record.get("_data"))
    for key in keys:
        value = _text(data.get(key))
        if value:
            return value
    return _text(record.get("_path")) or ""


def _latest(records: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any] | None:
    return max(records, key=lambda item: _record_time(item, keys), default=None)


def _action_is_formal(value: dict[str, Any]) -> bool:
    return value.get("action") == FORMAL_ACTION and not bool(value.get("research_only"))


def _formal(value: dict[str, Any], buy_allowed: bool) -> bool:
    return buy_allowed and bool(value.get("buy_enabled", True)) and _action_is_formal(value)


def _assessment_candidates(review: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer an optional assessment's matching candidate corrections when present."""
    assessment = _as_dict(review.get("assessment"))
    assessment_at = _text(assessment.get("as_of")) or _text(assessment.get("created_at"))
    review_at = _text(review.get("as_of")) or _text(_as_dict(review.get("snapshot")).get("as_of"))
    if assessment_at and review_at:
        assessment_time, review_time = _parse_time(assessment_at), _parse_time(review_at)
        if assessment_time and review_time and assessment_time < review_time:
            return []
    values = _as_list(assessment.get("candidates")) or _as_list(_as_dict(assessment.get("decision")).get("stocks"))
    return [item for item in values if isinstance(item, dict)]


def _corrected_candidates(review: dict[str, Any]) -> list[dict[str, Any]]:
    raw_value = review.get("candidates")
    # A current assessment can stand in only when the original candidate list
    # is absent.  An explicit empty list is evidence of an empty pool, not an
    # invitation to resurrect an older assessment.
    raw = [item for item in raw_value if isinstance(item, dict)] if isinstance(raw_value, list) else _assessment_candidates(review)
    corrections = {item.get("code"): item for item in _assessment_candidates(review) if _text(item.get("code"))}
    result = []
    for item in raw:
        correction = corrections.get(item.get("code"), {})
        result.append({**item, **correction})
    return result


def _snapshot_candidate_evidence(review: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Accept a complete candidate list only; partial data cannot prove removals."""
    raw = review.get("candidates")
    if not isinstance(raw, list):
        return [], False, "归档候选列表格式无效，不能比较候选变动"
    source = raw
    if len(source) > MAX_CANDIDATES:
        return [], False, f"归档候选数量超过上限 {MAX_CANDIDATES}，不能用截断数据比较变动"
    if any(not isinstance(item, dict) or not _text(item.get("code")) for item in source):
        return [], False, "归档候选含无效条目，不能比较候选变动"
    corrected = _corrected_candidates(review)
    codes = [item.get("code") for item in corrected]
    if len(corrected) != len(source) or len(codes) != len(set(codes)):
        return [], False, "归档候选存在重复或不完整条目，不能比较候选变动"
    return corrected, True, None


def _quote_sample(value: dict[str, Any], fallback_time: str) -> dict[str, Any] | None:
    price = _number(value.get("price"))
    time = _text(value.get("quote_time")) or _text(value.get("time")) or fallback_time
    if price is None or not time:
        return None
    return {"time": time, "price": price}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if len(value) == 14 and value.isdigit():
            parsed = datetime.strptime(value, "%Y%m%d%H%M%S")
        else:
            parsed = datetime.fromisoformat(value)
        return parsed.astimezone(SHANGHAI).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _after(left: str | None, right: str | None) -> bool:
    """Return false for malformed timestamps rather than making a time claim."""
    left_time, right_time = _parse_time(left), _parse_time(right)
    return bool(left_time and right_time and left_time > right_time)


def _not_future(value: str, now: datetime) -> bool:
    parsed = _parse_time(value)
    return bool(parsed and parsed <= now)


def _candidate_history(reviews: list[dict[str, Any]], code: str, event_time: str) -> list[dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {}
    for record in reviews:
        data = _as_dict(record.get("_data"))
        fallback_time = _record_time(record, ("as_of",))
        candidates = _corrected_candidates(data)
        match = next((item for item in candidates if item.get("code") == code), None)
        if match:
            sample = _quote_sample(match, fallback_time)
        else:
            snapshot = _as_dict(data.get("snapshot"))
            quote = _as_dict(_as_dict(snapshot.get("quotes")).get(code))
            sample = _quote_sample(quote, _text(snapshot.get("as_of")) or fallback_time)
        if sample and (not event_time or sample["time"] <= event_time):
            samples[sample["time"]] = sample
    return [samples[key] for key in sorted(samples)[-MAX_HISTORY:]]


def _candidate(value: dict[str, Any], *, buy_allowed: bool, reviews: list[dict[str, Any]], snapshot: dict[str, Any],
               archive_strategy: dict[str, Any], source_report_id: str | None) -> dict[str, Any] | None:
    code = _text(value.get("code"))
    if not code:
        return None
    quote = _as_dict(_as_dict(snapshot.get("quotes")).get(code))
    industry = _text(value.get("industry")) or _text(_as_dict(snapshot.get("industries")).get(code))
    event_time = _text(value.get("quote_time")) or _text(quote.get("time")) or ""
    verified_core = value.get("core") == "主线核心" and value.get("alignment") == "匹配主线"
    formal = _formal(value, buy_allowed)
    if formal and verified_core:
        rank_basis = "正式候选：归档标注主线核心且匹配主线"
    elif formal:
        rank_basis = "正式候选：当前周期硬规则允许；未见可核验主线核心标注"
    else:
        rank_basis = "研究状态排序，非模型投资优先级"
    output = {
        "code": code,
        "name": _text(value.get("name")) or _text(quote.get("name")),
        "rank": _number(value.get("rank")),
        "status": _text(value.get("status")),
        "price": _number(value.get("price")) if _number(value.get("price")) is not None else _number(quote.get("price")),
        "vwap": _number(value.get("vwap")) if _number(value.get("vwap")) is not None else _number(quote.get("vwap")),
        "pct": _number(value.get("pct")) if _number(value.get("pct")) is not None else _number(quote.get("pct")),
        "quote_time": event_time or None,
        "industry": industry,
        "reason": _text(value.get("reason")),
        "entry_condition": _text(value.get("entry_condition")),
        "invalidation": _text(value.get("invalidation")),
        "missing": [item for item in _as_list(value.get("missing")) if isinstance(item, str)],
        "research_only": bool(value.get("research_only")),
        "formal_candidate": formal,
        "rank_basis": rank_basis,
        "history": _candidate_history(reviews, code, event_time),
        "strategy": _candidate_strategy(value, archive_strategy),
        "source_report_id": source_report_id,
    }
    if output["formal_candidate"] and _number(value.get("budget")) is not None:
        output["budget"] = _number(value.get("budget"))
    return output


def _quote_observations(records: list[dict[str, Any]], code: str, time_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {}
    for record in records:
        data = _as_dict(record.get("_data"))
        snapshot = _as_dict(data.get("snapshot"))
        quote = _as_dict(_as_dict(snapshot.get("quotes")).get(code))
        sample = _quote_sample(quote, _text(snapshot.get("as_of")) or _record_time(record, time_keys))
        if sample:
            samples[sample["time"]] = sample
    return [samples[key] for key in sorted(samples)]


def _recommendation_performance(recommendations: list[dict[str, Any]], reports: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quote observations after each immutable recommendation; never a fill or profit claim."""
    result: list[dict[str, Any]] = []
    for recommendation in recommendations:
        now = datetime.now()
        observations = [sample for sample in (
            _quote_observations(reports, recommendation["code"], ("created_at", "as_of"))
            + _quote_observations(reviews, recommendation["code"], ("as_of", "created_at"))
        ) if _after(sample["time"], recommendation["recommended_at"])
           and _not_future(sample["time"], now)]
        unique = {sample["time"]: sample for sample in observations}
        ordered = [unique[key] for key in sorted(unique)]
        prices = [sample["price"] for sample in ordered]
        first = prices[0] if prices else None
        last = prices[-1] if prices else None
        baseline = _number(recommendation["price"])
        result.append({
            "recommendation_id": recommendation["id"], "code": recommendation["code"],
            "observations": len(ordered), "first_price": first, "last_price": last,
            "high_price": max(prices) if prices else None, "low_price": min(prices) if prices else None,
            "change_pct": ((last - baseline) / baseline * 100) if baseline not in (None, 0) and last is not None else None,
        })
    return result


def _review_model_valid(review: dict[str, Any]) -> bool:
    """Only an explicit successful rolling-model result can authorize current state."""
    return _text(review.get("jev_status")) == "success" and not _text(review.get("error_type"))


def _market_phase(report: dict[str, Any], cycle_is_current: bool) -> tuple[str | None, str | None]:
    if report.get("status") != "success" or not cycle_is_current:
        return None, None
    answer = _as_dict(_as_dict(_as_dict(report.get("jev")).get("answers")).get("market"))
    choice = _text(answer.get("choice"))
    if answer.get("type") != "choice" or choice not in MARKET_PHASES:
        return None, None
    report_id = _text(report.get("report_id"))
    return choice, f"cycle_jev:{report_id}" if report_id else "cycle_jev"


def _recommendations(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(reports, key=lambda item: _record_time(item, ("created_at", "as_of"))):
        data = _as_dict(record.get("_data"))
        decision = _as_dict(data.get("decision"))
        if data.get("status") != "success" or not bool(decision.get("buy_allowed")):
            continue
        report_id = _text(data.get("report_id")) or _text(record.get("_path"))
        recommended_at = _text(data.get("created_at")) or _text(data.get("as_of"))
        for stock in _as_list(decision.get("stocks")):
            if not isinstance(stock, dict) or not _formal(stock, True):
                continue
            code = _text(stock.get("code"))
            if not code or not report_id or not recommended_at:
                continue
            identifier = f"{report_id}:{code}"
            if identifier in seen:
                continue
            seen.add(identifier)
            event = {
                "id": identifier, "report_id": report_id, "code": code,
                "name": _text(stock.get("name")), "recommended_at": recommended_at,
                "price": _number(stock.get("price")), "action": _text(stock.get("action")),
                "reason": _text(stock.get("reason")),
                "entry_condition": _text(stock.get("entry_condition")) or _text(stock.get("buy_reference")),
                "invalidation": _text(stock.get("invalidation")),
                "strategy_version": _text(data.get("strategy_version")) or _text(decision.get("strategy_version")),
                "expires_at": _text(data.get("expires_at")), "budget": _number(stock.get("budget")),
            }
            if "entry_type" in stock:
                event["entry_type"] = _text(stock.get("entry_type"))
            output.append(event)
    return output


def _recommendation_sources(reports: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep strategy provenance beside immutable recommendation event payloads."""
    sources: dict[str, dict[str, Any]] = {}
    for record in sorted(reports, key=lambda item: _record_time(item, ("created_at", "as_of"))):
        data = _as_dict(record.get("_data"))
        decision = _as_dict(data.get("decision"))
        if data.get("status") != "success" or not bool(decision.get("buy_allowed")):
            continue
        report_id = _text(data.get("report_id")) or _text(record.get("_path"))
        if not report_id:
            continue
        archive_strategy = _strategy(data.get("strategy"))
        for stock in _as_list(decision.get("stocks")):
            if not isinstance(stock, dict) or not _formal(stock, True):
                continue
            code = _text(stock.get("code"))
            if not code:
                continue
            sources.setdefault(f"{report_id}:{code}", {
                "source_report_id": report_id,
                "strategy": _candidate_strategy(stock, archive_strategy),
            })
    return sources


def _selection_history(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return bounded archive snapshots, retaining failed snapshots as unavailable evidence."""
    snapshots: list[dict[str, Any]] = []
    for record in sorted(reviews, key=lambda item: _record_time(item, ("as_of", "created_at")))[-MAX_HISTORY:]:
        data = _as_dict(record.get("_data"))
        snapshot = _as_dict(data.get("snapshot"))
        report_id = _text(data.get("report_id")) or _text(record.get("_path"))
        as_of = _text(data.get("as_of")) or _text(snapshot.get("as_of"))
        archive_strategy = _strategy(data.get("strategy"))
        raw_candidates, candidates_complete, evidence_reason = _snapshot_candidate_evidence(data)
        available = bool(report_id and as_of and _parse_time(as_of) and _review_model_valid(data) and candidates_complete)
        if not available:
            if not report_id or not as_of or not _parse_time(as_of):
                reason = "归档缺少报告编号或时间，不能比较候选变动"
            elif not candidates_complete:
                reason = evidence_reason
            else:
                reason = "滚动复盘模型未成功，不能将缺失候选解释为移出"
        else:
            reason = None
        candidates = []
        if available:
            for value in raw_candidates:
                code = _text(value.get("code"))
                if not code:
                    continue
                candidates.append({
                    "code": code,
                    "name": _text(value.get("name")),
                    "status": _text(value.get("status")),
                    "reason": _text(value.get("reason")),
                    "strategy": _candidate_strategy(value, archive_strategy),
                })
        snapshots.append({
            "report_id": report_id,
            "as_of": as_of,
            "available": available,
            "reason": reason,
            "strategy": _snapshot_strategy(raw_candidates, archive_strategy) if available else dict(UNKNOWN_STRATEGY),
            "candidates": candidates,
        })
    return snapshots


def _latest_strategy_comparison(history: list[dict[str, Any]], *, has_unreadable_gap: bool = False) -> dict[str, Any]:
    if has_unreadable_gap:
        result = compare_snapshots(None, None)
        result["reason"] = "存在无法读取的滚动复盘归档，默认策略对照存在时间缺口"
        return result
    if len(history) < 2:
        return compare_snapshots(None, history[-1] if history else None)
    return compare_snapshots(history[-2], history[-1])


def load_overview(data_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """Load a bounded frontend view without mutating archives or reading credentials."""
    root = Path(data_dir)
    now = now or datetime.now(SHANGHAI)
    if now.tzinfo:
        now = now.astimezone(SHANGHAI).replace(tzinfo=None)
    all_reports, report_warnings = _read_archives(root / "codex_monitor" / "reports", "*.json", "report", limit=None)
    reports = all_reports[-MAX_ARCHIVES:]
    if len(all_reports) > MAX_ARCHIVES:
        report_warnings.append(f"报告归档数量超过上限；当前概览仅使用最新 {MAX_ARCHIVES} 份")
    reviews, review_warnings = _read_archives(root / "reviews", "*rolling.json", "review")
    warnings = report_warnings + review_warnings
    latest_report = _latest(reports, ("created_at", "as_of"))
    latest_review = _latest(reviews, ("as_of", "created_at"))
    report_data = _as_dict(latest_report.get("_data")) if latest_report else {}
    review_data = _as_dict(latest_review.get("_data")) if latest_review else {}
    latest_failed = bool(latest_report) and report_data.get("status") != "success"
    if latest_failed:
        warnings.append(f"最新周期报告失败：{_text(report_data.get('report_id')) or latest_report.get('_path')}")
    if not all_reports and not reviews:
        source_status = "degraded" if warnings else "empty"
    else:
        source_status = "degraded" if warnings or latest_failed else "ok"
    if all_reports and not reviews:
        warnings.append("缺少最新滚动复盘；当前候选不可用")
        source_status = "degraded"
    elif reviews and not all_reports:
        warnings.append("缺少最新周期报告；当前买入已关闭")
        source_status = "degraded"
    if latest_review and not _review_model_valid(review_data):
        warnings.append("最新滚动复盘模型失败；等待有效复盘")
        source_status = "degraded"

    review_decision = _as_dict(review_data.get("decision"))
    report_decision = _as_dict(report_data.get("decision"))
    review_at = _text(review_data.get("as_of")) or _text(_as_dict(review_data.get("snapshot")).get("as_of"))
    report_at = _text(report_data.get("created_at")) or _text(report_data.get("as_of"))
    report_expires_at = _text(report_data.get("expires_at"))
    cycle_is_current = False
    review_time, report_time, expiry_time = _parse_time(review_at), _parse_time(report_at), _parse_time(report_expires_at)
    if review_time and report_time and expiry_time:
        report_age = (now - report_time).total_seconds()
        review_age = (now - review_time).total_seconds()
        cycle_is_current = (
            report_time <= review_time < expiry_time
            and 0 <= report_age <= CURRENT_CYCLE_SKEW_SECONDS
            and 0 <= review_age <= CURRENT_CYCLE_SKEW_SECONDS
            and now < expiry_time
        )
    if latest_review and latest_report and not cycle_is_current:
        warnings.append("最新滚动复盘与周期报告已过期或时间不一致；当前买入已关闭")
        source_status = "degraded"
    current_buy_allowed = (
        bool(latest_review)
        and bool(review_decision.get("buy_allowed"))
        and bool(latest_report)
        and report_data.get("status") == "success"
        and bool(report_decision.get("buy_allowed"))
        and cycle_is_current
        and _review_model_valid(review_data)
    )
    snapshot = _as_dict(review_data.get("snapshot"))
    review_strategy = _strategy(review_data.get("strategy"))
    review_report_id = _text(review_data.get("report_id")) or (_text(latest_review.get("_path")) if latest_review else None)
    emotion = {key: value for key, value in _as_dict(snapshot.get("emotion")).items()
               if key in {"date", "zt", "zb", "dt", "zb_rate", "max_lb"} and (_text(value) is not None or _number(value) is not None)}
    sectors = [
        {"name": name, "count": count}
        for name, count in sorted(_as_dict(snapshot.get("sector_counts")).items(), key=lambda item: (-_number(item[1]) if _number(item[1]) is not None else 0, str(item[0])))
        if isinstance(name, str) and _number(count) is not None
    ]
    preliminary_current_recommendation_ids = []
    if current_buy_allowed:
        preliminary_current_recommendation_ids = [event["id"] for event in _recommendations([latest_report])]
    current_codes = {identifier.rsplit(":", 1)[-1] for identifier in preliminary_current_recommendation_ids}
    candidates = [
        item for item in (
            _candidate(
                value,
                buy_allowed=current_buy_allowed and value.get("code") in current_codes,
                reviews=reviews,
                snapshot=snapshot,
                archive_strategy=review_strategy,
                source_report_id=review_report_id,
            )
            for value in _corrected_candidates(review_data)[:MAX_CANDIDATES]
        ) if item is not None
    ]
    candidates.sort(key=lambda item: (
        not item["formal_candidate"],
        not item["rank_basis"].startswith("正式候选：归档标注"),
        {"新增": 0, "保留": 0, "降权": 1, "移出": 2}.get(item["status"], 1),
        item["rank"] if item["rank"] is not None else MAX_CANDIDATES,
        item["code"],
    ))
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
    final_current_codes = {candidate["code"] for candidate in candidates if candidate["formal_candidate"]}
    current_recommendation_ids = [
        identifier for identifier in preliminary_current_recommendation_ids
        if identifier.rsplit(":", 1)[-1] in final_current_codes
    ]
    coverage_times = [_record_time(record, ("created_at", "as_of")) for record in all_reports]
    coverage_days = {value[:10] for value in coverage_times if len(value) >= 10}
    recommendations = _recommendations(all_reports)
    selection_history = _selection_history(reviews)
    phase, phase_source = _market_phase(report_data, cycle_is_current)
    return {
        "as_of": _text(review_data.get("as_of")) or _text(snapshot.get("as_of")) or _text(report_data.get("created_at")) or _text(report_data.get("as_of")),
        "source_status": source_status,
        "warnings": warnings,
        "market": {"emotion": emotion, "sectors": sectors, "phase": phase, "phase_source": phase_source},
        "candidates": candidates,
        "risk_reasons": [item for item in _as_list(review_decision.get("risk_reasons") or report_decision.get("risk_reasons")) if isinstance(item, str)],
        "recommendations": recommendations,
        "recommendation_sources": _recommendation_sources(all_reports),
        "current_recommendation_ids": current_recommendation_ids,
        "recommendation_performance": _recommendation_performance(recommendations, all_reports, reviews),
        "selection_history": selection_history,
        "strategy_comparison": _latest_strategy_comparison(selection_history, has_unreadable_gap=bool(review_warnings)),
        "positions": [],
        "model_status": _text(review_data.get("jev_status")) or _text(_as_dict(report_data.get("jev")).get("model")),
        "coverage": {"reports": len(all_reports), "days": len(coverage_days)},
        "broker": {"name": "国金证券", "connected": False, "live_enabled": False},
    }
