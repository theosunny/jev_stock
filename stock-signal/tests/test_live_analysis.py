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
    monkeypatch.setattr(live_analysis.quotes, "fetch_quotes", lambda codes: {c: quote("20260922102000") for c in codes})
    monkeypatch.setattr(live_analysis, "_emotion_for", lambda date: (snapshot()["emotion"], []))
    monkeypatch.setattr(live_analysis, "_previous_emotion", lambda date: (snapshot()["previous_emotion"], {}))
    with pytest.raises(live_analysis.SnapshotError, match="stale"):
        live_analysis.collect_snapshot(plan(), NOW)


def test_pool_rejects_empty_dt_when_total_is_nonzero(monkeypatch):
    monkeypatch.setattr(live_analysis.market, "_get_json", lambda url: {"data": {"pool": [], "tc": 2}})
    with pytest.raises(live_analysis.SnapshotError, match="DT"):
        live_analysis._pool("DT", "20260922")


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
    result = live_analysis.evaluate(snapshot(sector_counts={"医药": 3}), plan())
    assert result["stocks"][0]["action"] == "观察"


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
