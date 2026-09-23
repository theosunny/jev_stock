import json
import datetime as dt
from pathlib import Path

from assistant.data import load_overview


NOW = dt.datetime(2026, 9, 23, 10, 2)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def report(*, report_id="r1", created_at="2026-09-23T10:00:00", status="success",
           buy_allowed=True, stocks=None, **extra):
    return {
        "report_id": report_id,
        "created_at": created_at,
        "expires_at": "2026-09-23T10:03:00",
        "status": status,
        "snapshot": {
            "as_of": created_at,
            "emotion": {"zt": 12, "zb": 2},
            "sector_counts": {"半导体": 4, "医药": 2},
            "quotes": {"sz000001": {"price": 10.2, "time": "20260923100000"}},
            "industries": {"sz000001": "半导体"},
        },
        "decision": {"buy_allowed": buy_allowed, "risk_reasons": [], "stocks": stocks or []},
        **extra,
    }


def review(*, report_id="rolling-1", as_of="2026-09-23T10:01:00", buy_allowed=True,
           candidates=None, **extra):
    return {
        "report_id": report_id,
        "as_of": as_of,
        "snapshot": {
            "emotion": {"zt": 13, "zb": 1},
            "sector_counts": {"半导体": 5},
            "quotes": {"sz000001": {"price": 10.3, "pct": 3.0, "time": "20260923100100"}},
            "industries": {"sz000001": "半导体"},
        },
        "candidates": candidates or [],
        "decision": {"buy_allowed": buy_allowed, "risk_reasons": []},
        "jev_status": "success",
        **extra,
    }


def formal_candidate(**extra):
    return {
        "code": "sz000001", "name": "样例", "rank": 1, "status": "确认",
        "price": 10.3, "vwap": 10.1, "quote_time": "20260923100100",
        "reason": "符合规则", "entry_condition": "站稳 VWAP", "invalidation": "跌破 VWAP",
        "missing": [], "research_only": False, "buy_enabled": True,
        "budget": 5000, "action": "买入候选", **extra,
    }


def test_load_overview_allowlists_current_data_and_formal_recommendations(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/old.json", report(
        stocks=[formal_candidate(price=10.0)], token="must-not-leak"))
    write_json(tmp_path / "codex_monitor/reports/new.json", report(
        report_id="r2", created_at="2026-09-23T10:00:30", buy_allowed=False,
        stocks=[formal_candidate(action="暂停买入", budget=9999)]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(
        candidates=[formal_candidate(), formal_candidate(code="sz000002", name="研究", research_only=True,
                                                          buy_enabled=False, budget=7777)]))

    result = load_overview(tmp_path, now=NOW)

    assert result["source_status"] == "ok"
    assert result["as_of"] == "2026-09-23T10:01:00"
    assert result["market"] == {
        "emotion": {"zt": 13, "zb": 1},
        "sectors": [{"name": "半导体", "count": 5}],
        "phase": None,
        "phase_source": None,
    }
    assert result["candidates"][0]["formal_candidate"] is False
    assert "budget" not in result["candidates"][0]
    assert "budget" not in result["candidates"][1]
    assert result["recommendations"] == [{
        "id": "r1:sz000001", "report_id": "r1", "code": "sz000001", "name": "样例",
        "recommended_at": "2026-09-23T10:00:00", "price": 10.0, "action": "买入候选",
        "reason": "符合规则", "entry_condition": "站稳 VWAP", "invalidation": "跌破 VWAP",
        "strategy_version": None, "expires_at": "2026-09-23T10:03:00", "budget": 5000,
    }]
    assert result["broker"] == {"name": "国金证券", "connected": False, "live_enabled": False}
    assert "must-not-leak" not in json.dumps(result, ensure_ascii=False)


def test_latest_failed_cycle_degrades_and_suppresses_current_permission(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/ok.json", report(stocks=[formal_candidate()]))
    write_json(tmp_path / "codex_monitor/reports/failure.json", report(
        report_id="failed", created_at="2026-09-23T10:05:00", status="error", buy_allowed=True,
        stocks=[formal_candidate()]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(candidates=[formal_candidate()]))

    result = load_overview(tmp_path, now=NOW)

    assert result["source_status"] == "degraded"
    assert result["candidates"][0]["formal_candidate"] is False
    assert "budget" not in result["candidates"][0]
    assert any("failed" in warning for warning in result["warnings"])
    assert [item["report_id"] for item in result["recommendations"]] == ["r1"]


def test_malformed_archives_empty_source_and_history_are_safe(tmp_path):
    reports = tmp_path / "codex_monitor/reports"
    reports.mkdir(parents=True)
    (reports / "broken.json").write_text("{not json", encoding="utf-8")
    write_json(tmp_path / "reviews/2026-09-23-100000-rolling.json", review(
        candidates=[formal_candidate()],
        assessment={"candidates": [formal_candidate(price=10.4, quote_time="20260923100200")]},
    ))
    write_json(tmp_path / "reviews/2026-09-23-100200-rolling.json", review(
        as_of="2026-09-23T10:02:00", candidates=[formal_candidate(price=10.5, quote_time="20260923100300")]))

    result = load_overview(tmp_path, now=NOW)

    assert result["source_status"] == "degraded"
    assert result["coverage"] == {"reports": 0, "days": 0}
    assert result["candidates"][0]["history"] == [
        {"time": "20260923100200", "price": 10.4},
        {"time": "20260923100300", "price": 10.5},
    ]
    assert all("token" not in key.lower() for key in result["candidates"][0])


def test_empty_directory_has_stable_empty_contract(tmp_path):
    result = load_overview(tmp_path, now=NOW)
    assert result["source_status"] == "empty"
    assert result["candidates"] == []
    assert result["positions"] == []
    assert result["recommendations"] == []
    assert result["market"] == {"emotion": {}, "sectors": [], "phase": None, "phase_source": None}


def test_recommendation_events_are_immutable_and_performance_uses_later_failed_quotes(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/formal.json", report(
        report_id="formal", stocks=[formal_candidate(price=10.0)]))
    write_json(tmp_path / "codex_monitor/reports/failed.json", report(
        report_id="failed", created_at="2026-09-23T10:02:00", status="error", buy_allowed=False,
        stocks=[], snapshot={"as_of": "2026-09-23T10:02:00", "quotes": {
            "sz000001": {"price": 11.0, "time": "20260923100200"}}}))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(
        candidates=[formal_candidate()], as_of="2026-09-23T10:01:00"))

    result = load_overview(tmp_path, now=NOW)

    assert result["recommendations"][0]["id"] == "formal:sz000001"
    assert result["recommendation_performance"] == [{
        "recommendation_id": "formal:sz000001", "code": "sz000001", "observations": 2,
        "first_price": 10.3, "last_price": 11.0, "high_price": 11.0, "low_price": 10.3,
        "change_pct": (11.0 - 10.0) / 10.0 * 100,
    }]
    assert result["candidates"][0]["formal_candidate"] is False
    assert result["candidates"][0]["rank_basis"] == "研究状态排序，非模型投资优先级"


def test_stale_cycle_never_exposes_current_formal_candidate(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report())
    write_json(tmp_path / "reviews/2026-09-23-103000-rolling.json", review(
        as_of="2026-09-23T10:30:00", candidates=[formal_candidate(quote_time="20260923103000")]))

    result = load_overview(tmp_path, now=dt.datetime(2026, 9, 23, 10, 30))

    assert result["source_status"] == "degraded"
    assert result["candidates"][0]["formal_candidate"] is False
    assert any("过期" in warning for warning in result["warnings"])


def test_latest_model_error_degrades_and_research_status_order_is_not_model_rank(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(
        buy_allowed=False,
        jev={"answers": {"market": {"type": "choice", "choice": "退潮"}}},
    ))
    candidates = [
        formal_candidate(code="sz000004", name="移出", status="移出", action="观察", research_only=True),
        formal_candidate(code="sz000003", name="降权", status="降权", action="观察", research_only=True),
        formal_candidate(code="sz000002", name="保留", status="保留", action="观察", research_only=True),
        formal_candidate(code="sz000001", name="新增", status="新增", action="观察", research_only=True),
    ]
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(
        candidates=candidates, jev_status="HTTPError", error_type="HTTPError"))

    result = load_overview(tmp_path, now=NOW)

    assert result["source_status"] == "degraded"
    assert result["market"]["phase"] == "退潮"
    assert result["market"]["phase_source"] == "cycle_jev:r1"
    assert [candidate["name"] for candidate in result["candidates"]] == ["新增", "保留", "降权", "移出"]
    assert {candidate["rank_basis"] for candidate in result["candidates"]} == {"研究状态排序，非模型投资优先级"}


def test_old_assessment_does_not_override_newer_raw_candidate(tmp_path):
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(
        candidates=[formal_candidate(price=10.5)],
        assessment={"as_of": "2026-09-23T10:00:00", "candidates": [formal_candidate(price=9.0)]},
    ))

    result = load_overview(tmp_path, now=NOW)

    assert result["candidates"][0]["price"] == 10.5


def test_model_error_disables_current_formal_candidate_even_when_both_decisions_allow(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(stocks=[formal_candidate()]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(
        candidates=[formal_candidate()], jev_status="error", error_type="network"))

    result = load_overview(tmp_path, now=NOW)

    assert result["source_status"] == "degraded"
    assert result["candidates"][0]["formal_candidate"] is False
    assert "budget" not in result["candidates"][0]


def test_recommendations_retain_oldest_formal_event_after_current_archive_limit(tmp_path):
    for index in range(1_001):
        write_json(tmp_path / f"codex_monitor/reports/{index:04d}.json", report(
            report_id=f"r{index}", stocks=[formal_candidate(price=index + 1)]))

    result = load_overview(tmp_path, now=NOW)

    assert len(result["recommendations"]) == 1_001
    assert result["recommendations"][0]["report_id"] == "r0"
    assert result["coverage"]["reports"] == 1_001


def test_current_recommendation_ids_require_fresh_report_then_successful_review(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(stocks=[formal_candidate()]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(candidates=[formal_candidate()]))

    result = load_overview(tmp_path, now=dt.datetime(2026, 9, 23, 10, 1, 30))

    assert result["current_recommendation_ids"] == ["r1:sz000001"]
    assert result["candidates"][0]["formal_candidate"] is True
    assert result["candidates"][0]["budget"] == 5000


def test_unknown_or_missing_review_model_status_fails_closed_for_current_buy(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(stocks=[formal_candidate()]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(
        candidates=[formal_candidate()], jev_status="available"))

    result = load_overview(tmp_path, now=dt.datetime(2026, 9, 23, 10, 1, 30))

    assert result["source_status"] == "degraded"
    assert result["current_recommendation_ids"] == []
    assert result["candidates"][0]["formal_candidate"] is False


def test_current_ids_exclude_report_event_when_latest_review_downgrades_it(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(stocks=[formal_candidate()]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(candidates=[
        formal_candidate(action="观察", research_only=True, buy_enabled=False, status="降权")
    ]))

    result = load_overview(tmp_path, now=dt.datetime(2026, 9, 23, 10, 1, 30))

    assert [event["id"] for event in result["recommendations"]] == ["r1:sz000001"]
    assert result["candidates"][0]["formal_candidate"] is False
    assert result["current_recommendation_ids"] == []


def test_recommendation_preserves_board_entry_type_for_conservative_ledger_handling(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(
        stocks=[formal_candidate(entry_type="排板候选")]))

    result = load_overview(tmp_path, now=NOW)

    assert result["recommendations"][0]["entry_type"] == "排板候选"


def test_explicit_null_entry_type_is_preserved_without_adding_one_to_older_events(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(
        stocks=[formal_candidate(entry_type=None)]))

    result = load_overview(tmp_path, now=NOW)

    assert result["recommendations"][0]["entry_type"] is None


def test_next_day_report_is_not_current_even_before_its_archive_expiry(tmp_path):
    write_json(tmp_path / "codex_monitor/reports/report.json", report(stocks=[formal_candidate()]))
    write_json(tmp_path / "reviews/2026-09-23-100100-rolling.json", review(candidates=[formal_candidate()]))

    result = load_overview(tmp_path, now=dt.datetime(2026, 9, 24, 10, 1))

    assert result["source_status"] == "degraded"
    assert result["current_recommendation_ids"] == []
    assert result["candidates"][0]["formal_candidate"] is False
