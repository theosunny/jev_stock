import datetime as dt
import json

from assistant.data import load_overview
from assistant.provenance import compare_snapshots


NOW = dt.datetime(2026, 9, 23, 10, 2)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def candidate(code="sz000001", name="样例", status="新增", **extra):
    return {"code": code, "name": name, "status": status, "reason": "归档理由", **extra}


def review(report_id, as_of, candidates, **extra):
    return {
        "report_id": report_id,
        "as_of": as_of,
        "jev_status": "success",
        "candidates": candidates,
        "snapshot": {"as_of": as_of},
        "decision": {"buy_allowed": False, "risk_reasons": []},
        **extra,
    }


def report(report_id="r1", **extra):
    return {
        "report_id": report_id,
        "created_at": "2026-09-23T10:00:00",
        "expires_at": "2026-09-23T10:03:00",
        "status": "success",
        "decision": {"buy_allowed": True, "stocks": [
            {"code": "sz000001", "name": "样例", "action": "买入候选", "buy_enabled": True},
        ]},
        **extra,
    }


def test_explicit_strategy_provenance_and_unknown_never_infers_catalog(tmp_path):
    strategy = {"id": "chen-v1", "version": "1.2", "name": "陈小群规则"}
    write_json(tmp_path / "codex_monitor/reports/r1.json", report(strategy=strategy))
    write_json(tmp_path / "reviews/one-rolling.json", review(
        "one", "2026-09-23T10:01:00", [candidate(strategy=strategy)], strategy=strategy,
    ))
    write_json(tmp_path / "reviews/two-rolling.json", review(
        "two", "2026-09-23T10:02:00", [candidate(code="sz000002", name="未知")],
    ))

    result = load_overview(tmp_path, now=NOW)

    assert result["recommendations"][0].get("strategy") is None
    assert result["recommendation_sources"]["r1:sz000001"] == {
        "source_report_id": "r1", "strategy": {"id": "chen-v1", "version": "1.2", "name": "陈小群规则", "label": "陈小群规则 · 1.2", "known": True},
    }
    assert result["candidates"][0]["strategy"] == {
        "id": None, "version": None, "name": None, "label": "来源未标注", "known": False,
    }
    assert result["candidates"][0]["source_report_id"] == "two"


def test_mixed_per_stock_strategy_metadata_does_not_claim_whole_pool_change(tmp_path):
    old = {"id": "short", "version": "1", "name": "短线"}
    new = {"id": "value", "version": "1", "name": "价值"}
    write_json(tmp_path / "reviews/one-rolling.json", review(
        "one", "2026-09-23T09:55:00", [candidate(status="新增", strategy=old), candidate("sz000002", "个股", strategy=new)], strategy=old,
    ))
    write_json(tmp_path / "reviews/two-rolling.json", review(
        "two", "2026-09-23T10:00:00", [candidate(status="保留", strategy=new), candidate("sz000003", "新增", strategy=new)], strategy=new,
    ))

    result = load_overview(tmp_path, now=NOW)
    comparison = result["strategy_comparison"]

    assert [entry["report_id"] for entry in result["selection_history"]] == ["one", "two"]
    assert result["selection_history"][0]["candidates"][1]["strategy"]["id"] == "value"
    assert comparison["available"] is True
    assert comparison["kind"] == "unknown"
    assert [(row["code"], row["before_status"], row["after_status"]) for row in comparison["added"]] == [("sz000003", None, "新增")]
    assert [(row["code"], row["before_status"], row["after_status"]) for row in comparison["removed"]] == [("sz000002", "新增", None)]
    assert [(row["code"], row["before_status"], row["after_status"]) for row in comparison["changed"]] == [("sz000001", "新增", "保留")]
    assert comparison["changed"][0]["before_strategy"]["id"] == "short"
    assert comparison["changed"][0]["after_strategy"]["id"] == "value"


def test_homogeneous_explicit_strategy_versions_produce_strategy_change():
    before = {
        "report_id": "one", "as_of": "2026-09-23T09:55:00", "available": True,
        "strategy": {"id": "short", "version": "1", "name": "短线", "label": "短线 · 1", "known": True}, "candidates": [],
    }
    after = {
        "report_id": "two", "as_of": "2026-09-23T10:00:00", "available": True,
        "strategy": {"id": "value", "version": "1", "name": "价值", "label": "价值 · 1", "known": True}, "candidates": [],
    }

    assert compare_snapshots(before, after)["kind"] == "strategy_change"


def test_failed_or_unavailable_snapshots_never_imply_removals(tmp_path):
    old = {"id": "short", "version": "1", "name": "短线"}
    write_json(tmp_path / "reviews/one-rolling.json", review("one", "2026-09-23T09:55:00", [candidate(strategy=old)], strategy=old))
    write_json(tmp_path / "reviews/two-rolling.json", review(
        "two", "2026-09-23T10:00:00", [], jev_status="HTTPError", error_type="HTTPError",
    ))

    result = load_overview(tmp_path, now=NOW)

    assert result["selection_history"][-1]["available"] is False
    assert "模型" in result["selection_history"][-1]["reason"]
    assert result["strategy_comparison"]["available"] is False
    assert result["strategy_comparison"]["removed"] == []


def test_missing_or_nonlist_candidate_evidence_is_unavailable_not_an_empty_pool(tmp_path):
    strategy = {"id": "short", "version": "1", "name": "短线"}
    write_json(tmp_path / "reviews/one-rolling.json", review("one", "2026-09-23T09:55:00", [candidate(strategy=strategy)], strategy=strategy))
    missing = review("two", "2026-09-23T10:00:00", [], strategy=strategy)
    missing.pop("candidates")
    write_json(tmp_path / "reviews/two-rolling.json", missing)

    missing_result = load_overview(tmp_path, now=NOW)
    assert missing_result["selection_history"][-1]["available"] is False
    assert missing_result["strategy_comparison"]["available"] is False
    assert missing_result["strategy_comparison"]["removed"] == []

    malformed = review("three", "2026-09-23T10:01:00", [], strategy=strategy)
    malformed["candidates"] = {"not": "a list"}
    write_json(tmp_path / "reviews/three-rolling.json", malformed)
    malformed_result = load_overview(tmp_path, now=NOW)
    assert malformed_result["selection_history"][-1]["available"] is False
    assert "格式无效" in malformed_result["selection_history"][-1]["reason"]


def test_assessment_subset_cannot_supply_missing_full_candidate_list(tmp_path):
    strategy = {"id": "short", "version": "1", "name": "短线"}
    first = review("one", "2026-09-23T09:55:00", [candidate(strategy=strategy)], strategy=strategy)
    second = review("two", "2026-09-23T10:00:00", [], strategy=strategy,
                    assessment={"as_of": "2026-09-23T10:00:00", "candidates": [candidate(status="保留", strategy=strategy)]})
    second.pop("candidates")
    write_json(tmp_path / "reviews/one-rolling.json", first)
    write_json(tmp_path / "reviews/two-rolling.json", second)

    result = load_overview(tmp_path, now=NOW)

    assert result["selection_history"][-1]["available"] is False
    assert result["strategy_comparison"]["available"] is False


def test_assessment_decision_subset_or_invalid_snapshot_time_never_imply_removals(tmp_path):
    strategy = {"id": "short", "version": "1", "name": "短线"}
    before = review("one", "2026-09-23T09:55:00", [
        candidate(code="sz000001", strategy=strategy), candidate(code="sz000002", name="第二只", strategy=strategy),
    ], strategy=strategy)
    partial = review("two", "2026-09-23T10:00:00", [], strategy=strategy,
                     assessment={"as_of": "2026-09-23T10:00:00", "decision": {"stocks": [candidate(strategy=strategy)]}})
    partial.pop("candidates")
    write_json(tmp_path / "reviews/one-rolling.json", before)
    write_json(tmp_path / "reviews/two-rolling.json", partial)
    result = load_overview(tmp_path, now=NOW)
    assert result["strategy_comparison"]["available"] is False
    assert result["strategy_comparison"]["removed"] == []

    invalid_time = review("three", "not-a-time", [candidate(strategy=strategy)], strategy=strategy)
    write_json(tmp_path / "reviews/three-rolling.json", invalid_time)
    invalid_result = load_overview(tmp_path, now=NOW)
    assert invalid_result["selection_history"][-1]["available"] is False
    assert "时间" in invalid_result["selection_history"][-1]["reason"]


def test_partial_duplicate_or_unreadable_history_blocks_default_comparison(tmp_path):
    strategy = {"id": "short", "version": "1", "name": "短线"}
    write_json(tmp_path / "reviews/one-rolling.json", review("one", "2026-09-23T09:55:00", [candidate(strategy=strategy)], strategy=strategy))
    write_json(tmp_path / "reviews/two-rolling.json", review(
        "two", "2026-09-23T10:00:00", [candidate(strategy=strategy), candidate(strategy=strategy)], strategy=strategy,
    ))
    duplicate_result = load_overview(tmp_path, now=NOW)
    assert duplicate_result["selection_history"][-1]["available"] is False
    assert "重复" in duplicate_result["selection_history"][-1]["reason"]

    (tmp_path / "reviews/broken-rolling.json").write_text("{", encoding="utf-8")
    unreadable_result = load_overview(tmp_path, now=NOW)
    assert unreadable_result["strategy_comparison"]["available"] is False
    assert "无法读取" in unreadable_result["strategy_comparison"]["reason"]


def test_compare_snapshots_rejects_reverse_or_unknown_contexts():
    unknown = {"id": None, "version": None, "name": None, "label": "来源未标注", "known": False}
    before = {"report_id": "two", "as_of": "2026-09-23T10:01:00", "available": True, "strategy": unknown, "candidates": []}
    after = {"report_id": "one", "as_of": "2026-09-23T10:00:00", "available": True, "strategy": unknown, "candidates": []}

    assert compare_snapshots(before, after)["available"] is False
    assert compare_snapshots(before, after)["kind"] == "unknown"
