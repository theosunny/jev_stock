"""Pure comparison helpers for strategy-labelled rolling research snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


UNKNOWN_STRATEGY = {
    "id": None,
    "version": None,
    "name": None,
    "label": "来源未标注",
    "known": False,
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(SHANGHAI).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _strategy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value.get("known"):
        return dict(UNKNOWN_STRATEGY)
    strategy_id, version, name = value.get("id"), value.get("version"), value.get("name")
    if not all(isinstance(item, str) and item for item in (strategy_id, version, name)):
        return dict(UNKNOWN_STRATEGY)
    return {"id": strategy_id, "version": version, "name": name, "label": value.get("label") or f"{name} · {version}", "known": True}


def _same_strategy(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left["known"] and right["known"] and (left["id"], left["version"]) == (right["id"], right["version"])


def _candidate_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = snapshot.get("candidates")
    if not isinstance(values, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or not item["code"]:
            continue
        result[item["code"]] = item
    return result


def _row(code: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before_strategy = _strategy(before.get("strategy") if before else None)
    after_strategy = _strategy(after.get("strategy") if after else None)
    before_reason = before.get("reason") if isinstance(before, dict) and isinstance(before.get("reason"), str) else None
    after_reason = after.get("reason") if isinstance(after, dict) and isinstance(after.get("reason"), str) else None
    return {
        "code": code,
        "name": (after or before or {}).get("name"),
        "before_status": before.get("status") if before else None,
        "after_status": after.get("status") if after else None,
        "before_strategy": before_strategy,
        "after_strategy": after_strategy,
        "before_reason": before_reason,
        "after_reason": after_reason,
        "reason": (
            after_reason if after is not None else
            "后轮未出现，源归档未提供移出原因" if before is not None else
            before_reason
        ),
    }


def compare_snapshots(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Compare two valid chronological snapshots without treating failures as removals."""
    empty = {"added": [], "removed": [], "retained": [], "changed": []}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"available": False, "reason": "缺少两份可比较的归档快照", "kind": "unknown", "before": before, "after": after, **empty}
    if not before.get("available") or not after.get("available"):
        return {"available": False, "reason": "快照不可用，不能将缺失候选解释为移出", "kind": "unknown", "before": before, "after": after, **empty}
    before_time, after_time = _timestamp(before.get("as_of")), _timestamp(after.get("as_of"))
    if not before_time or not after_time or before_time >= after_time:
        return {"available": False, "reason": "快照时间缺失或顺序无效", "kind": "unknown", "before": before, "after": after, **empty}

    before_strategy, after_strategy = _strategy(before.get("strategy")), _strategy(after.get("strategy"))
    kind = "strategy_change" if before_strategy["known"] and after_strategy["known"] and not _same_strategy(before_strategy, after_strategy) else (
        "same_strategy" if _same_strategy(before_strategy, after_strategy) else "unknown"
    )
    before_candidates, after_candidates = _candidate_map(before), _candidate_map(after)
    added = [_row(code, None, after_candidates[code]) for code in sorted(after_candidates.keys() - before_candidates.keys())]
    removed = [_row(code, before_candidates[code], None) for code in sorted(before_candidates.keys() - after_candidates.keys())]
    retained: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for code in sorted(before_candidates.keys() & after_candidates.keys()):
        row = _row(code, before_candidates[code], after_candidates[code])
        same_provenance = _same_strategy(row["before_strategy"], row["after_strategy"]) or (
            not row["before_strategy"]["known"] and not row["after_strategy"]["known"]
        )
        if row["before_status"] == row["after_status"] and same_provenance:
            retained.append(row)
        else:
            changed.append(row)
    return {
        "available": True,
        "reason": None,
        "kind": kind,
        "before": {"report_id": before.get("report_id"), "as_of": before.get("as_of"), "strategy": before_strategy},
        "after": {"report_id": after.get("report_id"), "as_of": after.get("as_of"), "strategy": after_strategy},
        "added": added,
        "removed": removed,
        "retained": retained,
        "changed": changed,
    }
