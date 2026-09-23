import json
from unittest.mock import patch

import pytest

from assistant import market_data


def quote_payload(code, name, price="10.20", pct="1.50", amount="123400"):
    fields = [""] * 49
    fields[1], fields[3], fields[32], fields[37], fields[30] = name, price, pct, amount, "20260923100000"
    fields[4], fields[5], fields[33], fields[34], fields[36] = "10.0", "10.1", "10.6", "9.8", "12000"
    fields[47], fields[48] = "11.0", "9.0"
    return 'v_%s="%s";' % (code, "~".join(fields))


def test_tencent_quote_parser_returns_requested_core_fields():
    parsed = market_data._parse_tencent_quotes(
        (quote_payload("sh000001", "上证指数") + quote_payload("sz000001", "样例股")).encode("gbk"))
    assert parsed["sh000001"]["open"] == 10.1
    assert parsed["sh000001"]["high"] == 10.6
    assert parsed["sh000001"]["prev_close"] == 10.0
    assert parsed["sh000001"]["vwap"] == pytest.approx(1028.333)
    assert parsed["sh000001"]["limit_up"] == 11.0


def test_stock_code_validation_accepts_only_sh_or_sz_six_digits():
    assert market_data._normalize_code("SZ000001") == "sz000001"
    for value in ("000001", "sh00001", "bj430047", "sh000001;bad", ""):
        with pytest.raises(ValueError):
            market_data._normalize_code(value)


def test_get_market_returns_warning_when_quote_provider_fails():
    market_data._clear_cache()
    with patch.object(market_data, "_fetch_bytes", side_effect=OSError("offline")):
        data = market_data.get_market()
    assert data["indices"] == []
    assert data["warnings"] == ["腾讯行情不可用"]
    assert data["as_of"]


def test_get_quote_is_short_cached_and_does_not_request_panels():
    market_data._clear_cache()
    raw = quote_payload("sz000001", "平安银行").encode("gbk")
    with patch.object(market_data, "_fetch_bytes", return_value=raw) as fetch:
        first = market_data.get_quote("sz000001")
        second = market_data.get_quote("sz000001")
    assert first["quote"]["price"] == 10.2
    assert second == first
    assert fetch.call_count == 1


def test_candles_accept_unadjusted_day_fallback():
    candles = market_data._parse_candles({"data": {"sh000001": {"day": [
        ["2026-09-22", "10", "10.5", "10.7", "9.9", "1000"],
    ]}}}, "sh000001")
    assert candles[0]["date"] == "2026-09-22"


def test_get_stock_parses_candles_and_financials_with_partial_provider_failure():
    market_data._clear_cache()
    quote = quote_payload("sz000001", "平安银行").encode("gbk")
    candles = {"data": {"sz000001": {"qfqday": [
        ["2026-09-22", "10", "10.5", "10.7", "9.9", "1000"],
    ]}}}
    income = {"result": {"data": [{
        "REPORT_DATE": "2026-06-30 00:00:00", "TOTAL_OPERATE_INCOME": 100.0,
        "PARENT_NETPROFIT": 20.0, "TOTAL_OPERATE_INCOME_YOY": 3.2,
        "PARENT_NETPROFIT_YOY": 4.1,
    }]}}
    cash = {"result": {"data": [{"REPORT_DATE": "2026-06-30 00:00:00", "NETCASH_OPERATE": 30.0}]}}

    def fetch_json(url, timeout=8):
        if "fqkline" in url:
            return candles
        if "GCASHFLOW" in url:
            return cash
        if "GINCOME" in url:
            return income
        raise AssertionError(url)

    with patch.object(market_data, "_fetch_bytes", return_value=quote), \
         patch.object(market_data, "_fetch_json", side_effect=fetch_json):
        data = market_data.get_stock("sz000001")
    assert data["quote"]["name"] == "平安银行"
    assert data["candles"][0]["close"] == 10.5
    assert data["financials"] == [{
        "date": "2026-06-30", "revenue": 100.0, "net_profit": 20.0,
        "revenue_growth": 3.2, "net_profit_growth": 4.1, "cash_flow": 30.0,
    }]
    assert data["warnings"] == []
    assert len(data["sources"]) == 3


def test_stock_keeps_income_when_cash_provider_fails_and_indices_skip_finance():
    market_data._clear_cache()
    raw = quote_payload("sh000001", "上证指数").encode("gbk")
    candles = {"data": {"sh000001": {"day": []}}}
    with patch.object(market_data, "_fetch_bytes", return_value=raw), \
         patch.object(market_data, "_fetch_json", return_value=candles) as fetch:
        index = market_data.get_stock("sh000001")
    assert index["financials"] == []
    assert len(index["sources"]) == 1
    assert fetch.call_count == 1


def test_cash_failure_keeps_income_period_with_empty_cash_flow():
    market_data._clear_cache()
    raw = quote_payload("sz000001", "平安银行").encode("gbk")
    income = {"result": {"data": [{"REPORT_DATE": "2026-06-30", "TOTAL_OPERATE_INCOME": 100,
                                    "PARENT_NETPROFIT": 20}]}}
    def fetch_json(url, timeout=8):
        if "fqkline" in url:
            return {"data": {"sz000001": {"qfqday": []}}}
        if "GINCOME" in url:
            return income
        raise OSError("cash offline")
    with patch.object(market_data, "_fetch_bytes", return_value=raw), \
         patch.object(market_data, "_fetch_json", side_effect=fetch_json):
        data = market_data.get_stock("sz000001")
    assert data["financials"][0]["date"] == "2026-06-30"
    assert data["financials"][0]["cash_flow"] is None
    assert "东方财富现金流量表不可用" in data["warnings"]
