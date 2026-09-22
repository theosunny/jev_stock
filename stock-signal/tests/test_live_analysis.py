import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_analysis


NOW = dt.datetime(2026, 9, 22, 10, 30)


def quote(time="20260922102930", price=10.0, **extra):
    value = {"name": "测试股", "price": price, "prev_close": 9.8, "open": 10.0,
             "high": 10.2, "low": 9.7, "pct": 2.0, "limit_up": 10.78,
             "limit_down": 8.82, "vwap": 9.95, "volume": 1000, "time": time}
    value.update(extra)
    return value


def plan(**stock_extra):
    stock = {"code": "sz000001", "name": "测试股", "enabled": True,
             "buy_enabled": True, "buy_anchor": "ma5", "buy_band_pct": 1,
             "buy_size_pct": 5, "stop_loss_pct": 6}
    stock.update(stock_extra)
    return {"总资金": 100000, "总仓位上限pct": 30, "watchlist": [stock]}


def snapshot(**overrides):
    value = {"as_of": NOW.isoformat(), "date": "2026-09-22",
             "quotes": {"sz000001": quote()}, "ma5": {"sz000001": 10.0},
             "industries": {"sz000001": "医药"},
             "emotion": {"date": "20260922", "zt": 20, "zb": 2, "dt": 1, "zb_rate": 9.1, "max_lb": 4},
             "previous_emotion": {"date": "20260921", "zt": 20, "zb": 2, "dt": 1, "zb_rate": 9.1, "max_lb": 4},
             "sector_counts": {"医药": 5}, "previous_sector_counts": {"医药": 5},
             "leaders": [], "news": [], "warnings": []}
    value.update(overrides)
    return value


def test_collect_snapshot_rejects_stale_quote(monkeypatch):
    def fetched(codes):
        values = {code: quote() for code in codes}
        values["sz000001"] = quote("20260922102000")
        return values
    monkeypatch.setattr(live_analysis.quotes, "fetch_quotes", fetched)
    monkeypatch.setattr(live_analysis, "_emotion_for", lambda date: (snapshot()["emotion"], []))
    monkeypatch.setattr(live_analysis, "_previous_emotion", lambda date: (snapshot()["previous_emotion"], {"leaders": []}))
    collected = live_analysis.collect_snapshot(plan(), NOW)
    assert "sz000001" not in collected["quotes"]
    assert any("stale" in message for message in collected["warnings"])


def test_stale_watch_stock_is_isolated_and_fresh_holding_still_stops(monkeypatch):
    p = plan(ref_price=10.0)
    p["watchlist"].append({"code": "sh600000", "name": "持仓股", "enabled": True,
                           "ref_price": 10.0, "buy_size_pct": 5, "stop_loss_pct": 6})
    def fetched(codes):
        values = {code: quote() for code in codes}
        values["sz000001"] = quote("20260921150000")
        values["sh600000"] = quote(price=9.3)
        return values
    emotion = snapshot()["emotion"]
    leaders = [{"hybk": "医药"}] * 5
    monkeypatch.setattr(live_analysis.quotes, "fetch_quotes", fetched)
    monkeypatch.setattr(live_analysis, "_emotion_for", lambda date: (emotion, leaders))
    monkeypatch.setattr(live_analysis, "_previous_emotion", lambda date: (emotion, {"leaders": leaders}))
    monkeypatch.setattr(live_analysis.market_news, "filter_news", lambda *args, **kwargs: [])
    collected = live_analysis.collect_snapshot(p, NOW)
    assert "sz000001" not in collected["quotes"]
    assert any("sz000001" in message for message in collected["warnings"])
    actions = {row["code"]: row["action"] for row in live_analysis.evaluate(collected, p)["stocks"]}
    assert actions == {"sz000001": "暂停买入", "sh600000": "止损提醒"}


def test_all_old_indices_mark_a_non_trading_day(monkeypatch):
    monkeypatch.setattr(live_analysis.quotes, "fetch_quotes", lambda codes: {
        code: quote("20260921150000") for code in codes})
    with pytest.raises(live_analysis.NonTradingDay):
        live_analysis.collect_snapshot(plan(), NOW)


def test_optional_context_returns_overnight_and_announcement_data(monkeypatch):
    monkeypatch.setattr(live_analysis.market, "overnight_line", lambda: "隔夜: 纳指+1%")
    monkeypatch.setattr(live_analysis.market, "announcements",
                        lambda code: {"risk": ["风险公告"], "good": ["利好公告"]})
    warnings = []
    overnight, announcements = live_analysis._optional_market_context(plan()["watchlist"], warnings)
    assert overnight == "隔夜: 纳指+1%"
    assert announcements["sz000001"]["risk"] == ["风险公告"]
    assert any("尽力扫描" in item for item in warnings)


def test_pool_rejects_empty_dt_when_total_is_nonzero(monkeypatch):
    monkeypatch.setattr(live_analysis.market, "_get_json", lambda url: {"data": {"pool": [], "tc": 2}})
    with pytest.raises(live_analysis.SnapshotError, match="DT"):
        live_analysis._pool("DT", "20260922")


def test_previous_emotion_keeps_all_leaders_for_sector_counts(monkeypatch):
    leaders = [{"hybk": "房地产服务"}] * 10 + [{"hybk": "房地产服务"}] * 2
    def fake_emotion(date):
        emotion = dict(snapshot()["emotion"], date=date)
        return emotion, leaders
    monkeypatch.setattr(live_analysis, "_emotion_for", fake_emotion)
    emotion, raw = live_analysis._previous_emotion("20260922")
    assert emotion["date"] == "20260921"
    assert len(raw["leaders"]) == 12


def test_close_quote_allows_1500_and_1501_minute():
    live_analysis._validate_quotes({"sz000001": quote("20260922150130")}, ["sz000001"],
                                   dt.datetime(2026, 9, 22, 15, 9))


def test_collect_snapshot_fails_closed_on_emotion_error(monkeypatch):
    monkeypatch.setattr(live_analysis.quotes, "fetch_quotes", lambda codes: {c: quote() for c in codes})
    monkeypatch.setattr(live_analysis, "_emotion_for", lambda date: (_ for _ in ()).throw(OSError("network down")))
    with pytest.raises(live_analysis.SnapshotError, match="emotion") as raised:
        live_analysis.collect_snapshot(plan(), NOW)
    assert raised.value.partial_snapshot["quotes"]["sz000001"]["price"] == 10.0
    assert raised.value.partial_snapshot["emotion"] == {}


def test_limit_up_stock_is_never_buy_candidate():
    s = snapshot(quotes={"sz000001": quote(price=10.78, limit_up=10.78)})
    result = live_analysis.evaluate(s, plan())
    assert result["buy_allowed"] is True
    assert result["stocks"][0]["action"] == "观察"
    assert "涨停" in result["stocks"][0]["reason"]


def test_individual_pause_prevents_buy():
    result = live_analysis.evaluate(snapshot(), plan(buy_enabled=False, pause_reason="板块轮出"))
    assert result["stocks"][0]["action"] == "暂停买入"
    assert "板块轮出" in result["stocks"][0]["reason"]


def test_sector_recovery_requirement_keeps_stock_under_observation():
    result = live_analysis.evaluate(snapshot(sector_counts={"医药": 3}, reentry_required=True), plan())
    assert result["stocks"][0]["action"] == "观察"


def test_normal_market_allows_active_sector_without_reentry_confirmation():
    result = live_analysis.evaluate(
        snapshot(sector_counts={"医药": 1}, previous_sector_counts={"医药": 0}), plan())
    assert result["stocks"][0]["action"] == "买入候选"
    assert result["reentry_confirmed"] is False


def test_reentry_requires_two_days_of_four_limit_ups():
    s = snapshot(sector_counts={"医药": 1}, previous_sector_counts={"医药": 5}, reentry_required=True)
    result = live_analysis.evaluate(s, plan())
    assert result["stocks"][0]["action"] == "观察"
    assert "恢复确认" in result["stocks"][0]["reason"]


def test_truncated_industry_name_matches_sector_count():
    s = snapshot(industries={"sz000001": "房地产服"},
                 sector_counts={"房地产服务": 5}, previous_sector_counts={"房地产服务": 5})
    assert live_analysis.evaluate(s, plan())["stocks"][0]["action"] == "买入候选"


def test_low_open_recovery_after_confirm_time_is_candidate():
    p = plan(buy_anchor=None, confirm={"open_pct_range": [2, 5], "after_time": "10:00",
                                       "price_above_open_pct": 1,
                                       "low_open": {"open_pct_range": [-2, 2], "after_time": "10:00"}})
    q = quote(price=10.05, prev_close=10.0, open=9.9, vwap=10.0)
    assert live_analysis.evaluate(snapshot(quotes={"sz000001": q}), p)["stocks"][0]["action"] == "买入候选"


def test_weak_to_strong_waits_until_configured_time():
    p = plan(buy_anchor=None, confirm={"open_pct_range": [2, 5], "after_time": "10:00",
                                       "price_above_open_pct": 1})
    q = quote(price=10.31, prev_close=10.0, open=10.2, vwap=10.1)
    s = snapshot(as_of="2026-09-22T09:59:00", quotes={"sz000001": q})
    assert live_analysis.evaluate(s, p)["stocks"][0]["action"] == "观察"


def test_missing_limit_up_or_invalid_emotion_fails_closed():
    s = snapshot(quotes={"sz000001": quote(limit_up=None)})
    assert live_analysis.evaluate(s, plan())["stocks"][0]["action"] == "暂停买入"
    s = snapshot(emotion={"date": "20260922", "zt": 20, "zb": 2, "dt": 1, "zb_rate": float("nan"), "max_lb": 4})
    assert live_analysis.evaluate(s, plan())["buy_allowed"] is False


def test_total_position_cap_prevents_buy():
    p = plan()
    p["watchlist"].append({"code": "sh600000", "name": "已持仓", "enabled": True,
                           "ref_price": 10, "buy_size_pct": 30})
    result = live_analysis.evaluate(snapshot(), p)
    assert result["buy_allowed"] is False
    assert "总仓位上限" in result["stocks"][0]["reason"]


def test_held_stock_stop_loss_still_alerts_during_ebb():
    p = plan(ref_price=10.0)
    s = snapshot(quotes={"sz000001": quote(price=9.3)},
                 emotion={"date": "20260922", "zt": 10, "zb": 4, "dt": 12, "zb_rate": 28.6, "max_lb": 2})
    result = live_analysis.evaluate(s, p)
    assert result["buy_allowed"] is False
    assert result["stocks"][0]["action"] == "止损提醒"


def test_missing_industry_does_not_crash_reentry_confirmation():
    result = live_analysis.evaluate(snapshot(industries={'sz000001': None}), plan())
    assert result['stocks'][0]['action'] != '买入候选'
    assert result['reentry_confirmed'] is False


def test_postmarket_source_timestamp_still_represents_same_day_close():
    now = dt.datetime(2026, 9, 22, 16, 0)
    live_analysis._ensure_quote_fresh('sh000001', dt.datetime(2026, 9, 22, 15, 14), now)
