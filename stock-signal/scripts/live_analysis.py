# -*- coding: utf-8 -*-
"""Fresh market snapshots and deterministic trading candidates for the Codex runner.

This module deliberately has no state, execution, or notification side effects.
"""
import datetime as dt
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import market
import news as market_news
import quotes


class SnapshotError(RuntimeError):
    """A required market datum was unavailable or inconsistent.

    ``partial_snapshot`` lets a runner retain already-validated positions for
    stop-loss/exit evaluation when a later market-wide request fails.
    """
    def __init__(self, message, partial_snapshot=None):
        super().__init__(message)
        self.partial_snapshot = partial_snapshot


class NonTradingDay(SnapshotError):
    """The quote source returned an older trading day's data."""


_INDEX_CODES = ("sh000001", "sz399001", "sz399006")
_POOL_SORT = {"ZT": "fbt:asc", "ZB": "fbt:asc", "DT": "fund:asc"}
_MARKET_PREFIX = {0: "sz", 1: "sh", 2: "bj"}


def _pool(kind, date):
    """Read and validate a single Eastmoney pool without market._pool's fallback."""
    if kind not in _POOL_SORT:
        raise ValueError("unsupported pool: %s" % kind)
    url = ("https://push2ex.eastmoney.com/getTopic%sPool?ut=%s&dpt=wz.ztzt"
           "&Pageindex=0&pagesize=320&sort=%s&date=%s" %
           (kind, market._UT, _POOL_SORT[kind].replace(":", "%3A"), date))
    try:
        response = market._get_json(url)
    except Exception as exc:
        raise SnapshotError("%s pool request failed: %s" % (kind, exc)) from exc
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise SnapshotError("%s pool has no data" % kind)
    pool, total = data.get("pool"), data.get("tc")
    if not isinstance(pool, list) or not isinstance(total, int) or total < 0:
        raise SnapshotError("%s pool is malformed" % kind)
    if len(pool) != total:
        raise SnapshotError("%s pool total conflict: tc=%s, pool=%s" % (kind, total, len(pool)))
    return pool


def _emotion_for(date):
    zt, zb, down = (_pool(kind, date) for kind in ("ZT", "ZB", "DT"))
    ladder = [int(row.get("lbc", 1) or 1) for row in zt]
    return ({"date": date, "zt": len(zt), "zb": len(zb), "dt": len(down),
             "zb_rate": round(100 * len(zb) / (len(zt) + len(zb)), 1) if zt or zb else 0.0,
             "max_lb": max(ladder) if ladder else 0}, zt)


def _previous_emotion(date):
    base = dt.datetime.strptime(date, "%Y%m%d").date()
    for delta in range(1, 11):
        candidate_date = base - dt.timedelta(days=delta)
        # Eastmoney commonly uses data=null on Saturday/Sunday.  Skip known
        # weekends, but treat a working-day null/error as unavailable data.
        if candidate_date.weekday() >= 5:
            continue
        candidate = candidate_date.strftime("%Y%m%d")
        try:
            emotion, leaders = _emotion_for(candidate)
        except SnapshotError:
            raise
        # A valid empty pool can be a holiday; a nonempty ZT pool establishes
        # the previous real market session without treating network errors as rest days.
        if emotion["zt"]:
            return emotion, {"date": candidate, "leaders": leaders}
    raise SnapshotError("no previous trading emotion within 10 calendar days")


def _quote_datetime(value):
    try:
        return dt.datetime.strptime(str(value), "%Y%m%d%H%M%S")
    except (TypeError, ValueError) as exc:
        raise SnapshotError("invalid quote timestamp: %r" % (value,)) from exc


def _is_auction_period(now):
    """Check if now is in morning auction window (09:15-09:30 Asia/Shanghai)"""
    import zoneinfo
    try:
        shanghai_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        if now.tzinfo is None:
            now_shanghai = now.replace(tzinfo=shanghai_tz)
        else:
            now_shanghai = now.astimezone(shanghai_tz)
        time_only = now_shanghai.time()
        return dt.time(9, 15) <= time_only < dt.time(9, 30)
    except Exception:
        # Fallback without timezone if zoneinfo fails
        time_only = now.time()
        return dt.time(9, 15) <= time_only < dt.time(9, 30)


def _validate_quotes(result, codes, now):
    """Validate a required quote set; used strictly for the market indices."""
    quotes_by_code = {code: _validated_quote(code, result.get(code), now) for code in codes}
    dates = {at.date() for _, at in quotes_by_code.values()}
    if dates != {now.date()}:
        if len(dates) == 1 and next(iter(dates)) < now.date():
            raise NonTradingDay("all market indices are from %s" % next(iter(dates)))
        raise SnapshotError("market index dates conflict or are not today")
    for code, (_, at) in quotes_by_code.items():
        _ensure_quote_fresh(code, at, now)
    return {code: quote for code, (quote, _) in quotes_by_code.items()}


def _validated_quote(code, quote, now):
    if not isinstance(quote, dict):
        raise SnapshotError("missing quote: %s" % code)
    at = _quote_datetime(quote.get("time"))
    is_auction = _is_auction_period(now)
    
    # Always require price and prev_close to be valid
    for field in ("price", "prev_close"):
        try:
            value = float(quote[field])
            if not math.isfinite(value) or value <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError("invalid %s for %s" % (field, code)) from exc
    
    # During auction, allow open/high/low to be 0 (not yet published)
    # Outside auction, require open/high/low > 0
    for field in ("open", "high", "low"):
        try:
            value = float(quote[field])
            if not math.isfinite(value):
                raise ValueError("not finite")
            # During auction, allow 0; outside auction, require > 0
            if value <= 0 and not is_auction:
                raise ValueError("requires > 0 outside auction")
        except (KeyError, TypeError) as exc:
            raise SnapshotError("invalid %s for %s%s" % (
                field, code, " (auction: open not yet published)" if is_auction else "")) from exc
        except ValueError as exc:
            if is_auction and value == 0:
                # Auction allows zero for open/high/low (not yet published)
                continue
            raise SnapshotError("invalid %s for %s%s" % (
                field, code, " (auction: open not yet published)" if is_auction else "")) from exc
    
    for field in ("pct", "volume", "vwap"):
        if quote.get(field) is None:
            continue
        try:
            value = float(quote[field])
            if not math.isfinite(value) or (field != "pct" and value < 0):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise SnapshotError("invalid %s for %s" % (field, code)) from exc
    return quote, at


def _ensure_quote_fresh(code, at, now):
    allowed_close = (now.time() >= dt.time(15, 0)
                     and at.date() == now.date()
                     and dt.time(15, 0) <= at.time() <= now.time())
    # Small source/collector clock skew is normal; a materially future quote is
    # not a valid observation for this cycle.  The post-close exception keeps
    # same-day official close updates usable after 15:00.
    age = (now - at).total_seconds()
    if not allowed_close and not -5 <= age <= 180:
        raise SnapshotError("stale quote: %s" % code)


def _optional_stock_data(codes, warnings):
    ma5, industries = {}, {}
    def get_ma5(code):
        return "ma5", code, quotes.fetch_ma5(code)
    def get_industry(code):
        return "industry", code, (market.stock_industry(code).get("industry") or None)
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(codes) * 2))) as executor:
        jobs = [executor.submit(get_ma5, code) for code in codes]
        jobs += [executor.submit(get_industry, code) for code in codes]
        for job in as_completed(jobs):
            try:
                kind, code, value = job.result()
            except Exception as exc:
                warnings.append("optional stock data unavailable: %s" % exc)
                continue
            if kind == "ma5":
                ma5[code] = value if isinstance(value, (int, float)) and value > 0 else None
                if ma5[code] is None:
                    warnings.append("MA5 unavailable: %s" % code)
            else:
                industries[code] = value
                if not value:
                    warnings.append("industry unavailable: %s" % code)
    return ma5, industries


def _optional_market_context(stocks, warnings):
    """Collect advisory context concurrently; failures never imply clean news."""
    announcements = {stock["code"]: {"risk": [], "good": []}
                     for stock in stocks if stock.get("code")}
    overnight = ""
    warnings.append("公告为尽力扫描，未发现风险不代表不存在风险")
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(stocks) + 1))) as executor:
        jobs = {executor.submit(market.overnight_line): ("overnight", None)}
        jobs.update({executor.submit(market.announcements, stock["code"]): ("announcement", stock["code"])
                     for stock in stocks if stock.get("code")})
        for job in as_completed(jobs):
            kind, code = jobs[job]
            try:
                value = job.result()
            except Exception as exc:
                warnings.append("%s unavailable%s: %s" % (kind, " for " + code if code else "", exc))
                continue
            if kind == "overnight":
                overnight = value if isinstance(value, str) else ""
            elif isinstance(value, dict):
                announcements[code] = {"risk": list(value.get("risk") or []),
                                       "good": list(value.get("good") or [])}
            else:
                warnings.append("announcement malformed for %s" % code)
    return overnight, announcements


def _pool_code(row):
    """Return a canonical code for an Eastmoney ZT row, or None if ambiguous."""
    try:
        raw = str(row.get("c", ""))
        market_id = int(row.get("m"))
    except (TypeError, ValueError):
        return None
    prefix = _MARKET_PREFIX.get(market_id)
    if not prefix or len(raw) != 6 or not raw.isdigit():
        return None
    return prefix + raw


def _finite_nonnegative(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _normalise_limit_pool(rows, warnings, now=None):
    """Retain auditable ZT evidence without treating it as an executable signal."""
    result = []
    for row in rows:
        if not isinstance(row, dict):
            warnings.append("limit-up pool row malformed")
            continue
        code = _pool_code(row)
        if not code:
            warnings.append("limit-up pool row has unsupported code")
            continue
        zttj = row.get("zttj") if isinstance(row.get("zttj"), dict) else {}
        try:
            normalized = {
                "code": code, "name": str(row.get("n") or code),
                "industry": str(row.get("hybk") or ""),
                "lbc": int(row.get("lbc", 0) or 0), "zbc": int(row.get("zbc", 0) or 0),
                "fbt": int(row.get("fbt", 0) or 0), "lbt": int(row.get("lbt", 0) or 0),
                "fund": float(row.get("fund", 0) or 0), "amount": float(row.get("amount", 0) or 0),
                "zttj": {"days": int(zttj.get("days", 0) or 0), "ct": int(zttj.get("ct", 0) or 0)},
            }
        except (TypeError, ValueError):
            warnings.append("limit-up pool row has invalid evidence: %s" % code)
            continue
        cutoff = int(now.strftime("%H%M%S")) if now else 150000
        if (normalized["lbc"] < 1 or normalized["zbc"] < 0 or normalized["fbt"] < 90000
                or normalized["lbt"] < normalized["fbt"] or normalized["lbt"] > 150000
                or (now is not None and normalized["lbt"] > cutoff)
                or not _finite_nonnegative(normalized["fund"]) or normalized["fund"] <= 0
                or not _finite_nonnegative(normalized["amount"]) or normalized["amount"] <= 0):
            warnings.append("limit-up pool row has incomplete evidence: %s" % code)
            continue
        result.append(normalized)
    return result


def _board_quote_shortlist(pool, required_codes=()):
    """Bound quote fan-out while preferring the highest continuity and liquidity evidence."""
    ranked = [row["code"] for row in sorted(
        pool, key=lambda row: (row["lbc"], row["zttj"]["ct"], row["amount"]), reverse=True)]
    required = [code for code in required_codes if code in ranked]
    return list(dict.fromkeys(required + ranked[:30]))


def _optional_board_quotes(pool, now, warnings, required_codes=()):
    codes = _board_quote_shortlist(pool, required_codes)
    if not codes:
        return {}
    try:
        raw = quotes.fetch_quotes(codes)
    except Exception as exc:
        warnings.append("limit-up candidate quotes unavailable: %s" % exc)
        return {}
    result = {}
    for code in codes:
        try:
            quote, at = _validated_quote(code, raw.get(code), now)
            if at.date() != now.date():
                raise SnapshotError("quote is not from today: %s" % code)
            _ensure_quote_fresh(code, at, now)
            result[code] = quote
        except SnapshotError as exc:
            warnings.append("limit-up candidate excluded: %s" % exc)
    return result


def collect_snapshot(plan, now):
    """Collect one internally consistent, fresh snapshot or raise fail-closed."""
    now = now.replace(tzinfo=None)
    stocks = [s for s in plan.get("watchlist", []) if s.get("enabled", True)]
    stock_codes = [s.get("code") for s in stocks if s.get("code")]
    codes = list(dict.fromkeys(_INDEX_CODES + tuple(stock_codes)))
    try:
        all_quotes = quotes.fetch_quotes(codes)
        index_quotes = _validate_quotes(all_quotes, _INDEX_CODES, now)
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError("quote request failed: %s" % exc) from exc
    snapshot = {"as_of": now.isoformat(), "date": now.strftime("%Y-%m-%d"),
                "quotes": dict(index_quotes), "ma5": {}, "industries": {},
                "emotion": {}, "previous_emotion": {}, "sector_counts": {},
                "previous_sector_counts": {}, "leaders": [], "news": [], "overnight": "",
                "announcements": {}, "limit_pool": [], "board_quotes": {}, "warnings": [], "errors": []}
    for code in stock_codes:
        try:
            quote, at = _validated_quote(code, all_quotes.get(code), now)
            if at.date() != now.date():
                raise SnapshotError("quote is not from today: %s" % code)
            _ensure_quote_fresh(code, at, now)
            snapshot["quotes"][code] = quote
        except SnapshotError as exc:
            message = "stock quote excluded: %s" % exc
            snapshot["warnings"].append(message)
            snapshot["errors"].append(message)
    snapshot["ma5"], snapshot["industries"] = _optional_stock_data(stock_codes, snapshot["warnings"])
    date = now.strftime("%Y%m%d")
    try:
        emotion, leaders = _emotion_for(date)
        previous, previous_raw = _previous_emotion(date)
        snapshot["emotion"], snapshot["previous_emotion"] = emotion, previous
        snapshot["leaders"] = leaders[:15]
        snapshot["sector_counts"] = dict(Counter(row.get("hybk") or "其他" for row in leaders))
        snapshot["previous_sector_counts"] = dict(Counter(
            row.get("hybk") or "其他" for row in previous_raw["leaders"]))
        snapshot["limit_pool"] = _normalise_limit_pool(leaders, snapshot["warnings"], now)
        snapshot["board_quotes"] = _optional_board_quotes(snapshot["limit_pool"], now, snapshot["warnings"], stock_codes)
    except Exception as exc:
        if not isinstance(exc, SnapshotError):
            exc = SnapshotError("market request failed: %s" % exc)
        snapshot["errors"].append(str(exc))
        raise SnapshotError("emotion unavailable: %s" % exc, snapshot) from exc
    try:
        snapshot["news"] = market_news.filter_news([s.get("name", "") for s in stocks], top=5)
    except Exception as exc:
        snapshot["warnings"].append("news unavailable: %s" % exc)
    snapshot["overnight"], snapshot["announcements"] = _optional_market_context(stocks, snapshot["warnings"])
    return snapshot


def _risk_reasons(snapshot):
    current, previous = snapshot.get("emotion") or {}, snapshot.get("previous_emotion") or {}
    if not current or not previous:
        return ["市场情绪数据缺失"]
    required = ("zt", "zb", "dt", "zb_rate", "max_lb")
    for emotion in (current, previous):
        for key in required:
            value = emotion.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                return ["市场情绪字段无效"]
    as_of = dt.datetime.fromisoformat(snapshot["as_of"])
    risk = previous if as_of.time() < dt.time(10, 0) else current
    reasons = []
    if risk.get("zb_rate", -1) >= 25:
        reasons.append("炸板率%.1f%%" % risk["zb_rate"])
    if risk.get("dt", -1) >= 10:
        reasons.append("跌停%d家" % risk["dt"])
    if as_of.time() >= dt.time(10, 0) and current.get("max_lb", 0) <= previous.get("max_lb", 0) - 2:
        reasons.append("高度板%d→%d" % (previous["max_lb"], current["max_lb"]))
    return reasons


def _is_limit_up(q):
    return q.get("limit_up") is not None and q["price"] >= q["limit_up"] * .998


def _sector_count(industry, counts):
    """Count a sector despite Eastmoney's occasional truncated industry label."""
    if not isinstance(industry, str) or not industry:
        return 0
    if industry in counts:
        return counts[industry]
    matches = [count for name, count in counts.items()
               if len(industry) >= 3 and (name.startswith(industry) or industry.startswith(name))]
    return max(matches, default=0)


def _after_time(value, now):
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return now.time() >= dt.time(hour, minute)
    except (AttributeError, TypeError, ValueError):
        return False


def _held_action(stock, q):
    ref = float(stock["ref_price"])
    stop = ref * (1 - float(stock.get("stop_loss_pct", 6)) / 100)
    if q["price"] <= stop:
        return "止损提醒", "现价%.2f跌破止损价%.2f" % (q["price"], stop)
    if _is_limit_up(q):
        return "兑现提醒", "触及涨停，一致加速按纪律兑现"
    if q.get("high", 0) >= ref * 1.05 and q["price"] <= q["high"] * .97:
        return "兑现提醒", "冲高后回落超过3%，按纪律兑现"
    return "持有", "持仓监控中；本版本不提供加仓建议"


def _at_limit_with_bid(quote):
    """Require a current sealed price and a nonzero best-buy observation.

    The observation says nothing about queue priority, cancellations, or whether
    a human order can be filled, so callers must retain an explicit manual
    order-book confirmation.
    """
    if not isinstance(quote, dict) or not _is_limit_up(quote):
        return False
    try:
        price, limit_up = float(quote.get("price")), float(quote["limit_up"])
        bid_price, bid_volume = float(quote.get("bid1_price")), float(quote.get("bid1_volume"))
        return (all(math.isfinite(value) for value in (price, limit_up, bid_price, bid_volume))
                and limit_up > 0 and bid_volume > 0
                and abs(price - limit_up) < .005 and abs(bid_price - limit_up) < .005)
    except (TypeError, ValueError, KeyError):
        return False


def _board_maps(snapshot):
    current = {row.get("code"): row for row in snapshot.get("limit_pool", [])
               if isinstance(row, dict) and row.get("code")}
    previous = {row.get("code"): row for row in snapshot.get("previous_limit_pool", [])
                if isinstance(row, dict) and row.get("code")}
    return current, previous


def _valid_board_timing(row, as_of):
    """Validate Eastmoney's HHMMSS fields against the corresponding snapshot."""
    try:
        at = dt.datetime.fromisoformat(as_of)
        first, last = int(row.get("fbt")), int(row.get("lbt"))
        for value in (first, last):
            encoded = "%06d" % value
            dt.time(int(encoded[:2]), int(encoded[2:4]), int(encoded[4:]))
        cutoff = int(at.strftime("%H%M%S"))
    except (TypeError, ValueError):
        return False
    return 90000 <= first <= last <= min(150000, cutoff)


def _positive_pool_money(row):
    return all(_finite_nonnegative(row.get(field)) and row.get(field, 0) > 0
               for field in ("fund", "amount"))


def _board_quote_time(quote, as_of):
    """Reject stale quotes; allow five seconds of source/collector clock skew."""
    try:
        observed = _quote_datetime(quote.get("time"))
        snapshot_at = dt.datetime.fromisoformat(as_of)
    except (AttributeError, TypeError, ValueError, SnapshotError):
        return None
    age = (snapshot_at - observed).total_seconds()
    return observed if -5 <= age <= 180 else None


def _board_entry_evidence(row, previous, quote, previous_quote, as_of, previous_as_of):
    """Return (entry_type, evidence, missing) from two successful snapshots."""
    missing = []
    if not previous:
        missing.append("缺少上一成功轮次涨停池证据")
    if not _at_limit_with_bid(quote):
        missing.append("当前涨停价或买一封单未同时验证")
    if previous and not _at_limit_with_bid(previous_quote):
        missing.append("上一轮涨停价或买一封单未验证")
    if not _positive_pool_money(row):
        missing.append("涨停池封板资金或成交金额字段缺失或为零")
    if not _valid_board_timing(row, as_of):
        missing.append("当前首次/最后封板时间字段无效或晚于快照")
    if previous and not _positive_pool_money(previous):
        missing.append("上一轮涨停池封板资金或成交金额字段缺失或为零")
    if previous and not _valid_board_timing(previous, previous_as_of):
        missing.append("上一轮首次/最后封板时间字段无效")
    current_quote_at = _board_quote_time(quote, as_of)
    previous_quote_at = _board_quote_time(previous_quote, previous_as_of) if previous else None
    if not current_quote_at:
        missing.append("当前买一报价时间无效、过期或晚于快照")
    if previous and not previous_quote_at:
        missing.append("上一轮买一报价时间无效、过期或晚于快照")
    if previous and current_quote_at and previous_quote_at and not (
            previous_quote_at <= dt.datetime.fromisoformat(previous_as_of) + dt.timedelta(seconds=5)
            < current_quote_at <= dt.datetime.fromisoformat(as_of) + dt.timedelta(seconds=5)
            and dt.datetime.fromisoformat(previous_as_of) < current_quote_at):
        missing.append("两轮买一报价时间未形成先后顺序，不能复用同一证据")
    if missing:
        return None, [], missing
    evidence = ["当前及上一成功轮次均在涨停池", "当前/上一轮涨停价与买一封单均有记录",
                "涨停池封板资金字段非零"]
    if row["zbc"] == 0 and previous["zbc"] == 0:
        return "排板候选", evidence + ["两轮均未记录开板次数"], []
    if row["zbc"] > previous["zbc"] or row["lbt"] > previous["lbt"]:
        return "回封候选", evidence + ["开板次数增加或最后封板时间更新"], []
    return None, evidence, ["未形成可验证的连续封板或本轮回封变化"]


def _board_result(row, previous, quote, previous_quote, config, risk_reasons, held_pct, cap, snapshot):
    entry_type, evidence, missing = _board_entry_evidence(
        row, previous, quote, previous_quote, snapshot.get("as_of"), snapshot.get("previous_board_as_of"))
    base = {"code": row["code"], "name": row["name"], "price": quote.get("price") if quote else None,
            "pct": quote.get("pct") if quote else None, "vwap": quote.get("vwap") if quote else None,
            "industry": row["industry"], "entry_type": entry_type,
            "entry_evidence": evidence, "missing_confirmation": missing}
    if not config:
        return {**base, "action": "观察", "research_only": True,
                "reason": "新发现涨停池标的未配置正式计划仓位；" + "；".join(missing or ["仅作研究观察"]) }
    reasons = list(risk_reasons)
    if not config.get("buy_enabled", True):
        reasons.append(config.get("pause_reason") or "个股买入已暂停")
    desired = float(config.get("buy_size_pct", 0) or 0)
    if desired <= 0 or desired > 20:
        reasons.append("单票仓位超过20%或未配置")
    if held_pct + desired > cap:
        reasons.append("总仓位上限不足")
    current_count = _sector_count(row["industry"], snapshot.get("sector_counts") or {})
    previous_count = _sector_count(row["industry"], snapshot.get("previous_sector_counts") or {})
    if current_count < 1:
        reasons.append("行业当日无涨停，主线未活跃")
    elif snapshot.get("reentry_required") and not (current_count >= 4 and previous_count >= 4):
        reasons.append("退潮后恢复确认未满足：行业涨停需连续两日各>=4家")
    if missing:
        reasons.extend(missing)
    if reasons:
        action = "暂停买入" if risk_reasons or not config.get("buy_enabled", True) else "观察"
    else:
        action = "买入候选"
    reason = "；".join(reasons) if reasons else (
        "%s证据满足；须人工盘口复核卖盘、封单变化、个人排队、撤单及公告监管；不保证成交" % entry_type)
    return {**base, "action": action, "reason": reason, "research_only": False}


def evaluate(snapshot, plan):
    """Turn a snapshot into explainable hard-rule candidates; never places orders."""
    risk_reasons = _risk_reasons(snapshot)
    held_pct = sum(float(s.get("buy_size_pct", 0)) for s in plan.get("watchlist", []) if s.get("ref_price"))
    cap = float(plan.get("总仓位上限pct", 30))
    reentry_required = bool(snapshot.get("reentry_required", False))
    if held_pct >= cap:
        risk_reasons.append("总仓位上限已用尽")
    results = []
    pool, previous_pool = _board_maps(snapshot)
    board_quotes = snapshot.get("board_quotes") or {}
    previous_board_quotes = snapshot.get("previous_board_quotes") or {}
    configured = {stock.get("code"): stock for stock in plan.get("watchlist", []) if stock.get("code")}
    for stock in plan.get("watchlist", []):
        if not stock.get("enabled", True):
            continue
        code, q = stock.get("code"), (snapshot.get("quotes") or {}).get(stock.get("code"))
        if code in pool and not stock.get("ref_price"):
            # Board-entry rules use their dedicated live quote and prior-poll
            # evidence, not the ordinary MA5/weak-to-strong path.
            results.append(_board_result(pool[code], previous_pool.get(code), board_quotes.get(code),
                                         previous_board_quotes.get(code), stock, risk_reasons,
                                         held_pct, cap, snapshot))
            continue
        if not q:
            results.append({"code": code, "name": stock.get("name", code), "action": "暂停买入", "reason": "行情数据缺失"})
            continue
        base = {"code": code, "name": stock.get("name", code), "price": q["price"], "pct": q.get("pct"), "vwap": q.get("vwap")}
        if stock.get("ref_price"):
            action, reason = _held_action(stock, q)
            results.append(dict(base, action=action, reason=reason))
            continue
        reasons = list(risk_reasons)
        if not stock.get("buy_enabled", True):
            reasons.append(stock.get("pause_reason") or "个股买入已暂停")
        if q.get("limit_up") is None:
            reasons.append("涨停价数据缺失")
        elif _is_limit_up(q):
            reasons.append("涨停或一字板，不追买")
        industry = (snapshot.get("industries") or {}).get(code)
        sector_ok = False
        if not industry:
            reasons.append("行业数据缺失")
        else:
            current_count = _sector_count(industry, snapshot.get("sector_counts") or {})
            previous_count = _sector_count(industry, snapshot.get("previous_sector_counts") or {})
            reentry_ok = current_count >= 4 and previous_count >= 4
            if current_count < 1:
                reasons.append("行业当日无涨停，主线未活跃")
            elif reentry_required and not reentry_ok:
                reasons.append("退潮后恢复确认未满足：行业涨停需连续两日各>=4家")
            else:
                sector_ok = True
        desired = float(stock.get("buy_size_pct", 0))
        if desired <= 0 or desired > 20:
            reasons.append("单票仓位超过20%或未配置")
        if held_pct + desired > cap:
            reasons.append("总仓位上限不足")
        ma5 = (snapshot.get("ma5") or {}).get(code)
        technical = False
        if stock.get("buy_anchor") == "ma5":
            if not ma5:
                reasons.append("MA5数据缺失")
            else:
                band = float(stock.get("buy_band_pct", 1)) / 100
                technical = ma5 * (1 - band) <= q["price"] <= ma5 * (1 + band)
                if not technical:
                    reasons.append("未进入MA5低吸区")
        elif stock.get("confirm"):
            c = stock["confirm"]
            open_pct = (q["open"] / q["prev_close"] - 1) * 100
            lo, hi = c.get("open_pct_range", [2, 5])
            high_open = (lo <= open_pct <= hi and _after_time(c.get("after_time", "10:00"),
                                                               dt.datetime.fromisoformat(snapshot["as_of"]))
                         and q["price"] >= q["open"] * (1 + float(c.get("price_above_open_pct", 1)) / 100))
            low = c.get("low_open") or {}
            low_open = (low.get("open_pct_range", []) and len(low["open_pct_range"]) == 2
                        and low["open_pct_range"][0] <= open_pct <= low["open_pct_range"][1]
                        and _after_time(low.get("after_time", c.get("after_time", "10:00")),
                                        dt.datetime.fromisoformat(snapshot["as_of"]))
                        and q["price"] >= q["prev_close"])
            technical = (high_open or low_open) and q.get("vwap") is not None and q["price"] >= q["vwap"]
            if technical:
                reasons.append("单一快照高于VWAP，需人工确认持续性")
            else:
                reasons.append("弱转强条件未满足")
        else:
            reasons.append("未配置买入规则")
        blocked = bool(risk_reasons or not stock.get("buy_enabled", True) or q.get("limit_up") is None
                       or held_pct + desired > cap or desired <= 0 or desired > 20)
        if _is_limit_up(q) and not risk_reasons and stock.get("buy_enabled", True):
            action = "观察"
        elif blocked:
            action = "暂停买入"
        elif technical and sector_ok:
            action = "买入候选"
        else:
            action = "观察"
        results.append(dict(base, action=action, reason="；".join(reasons) if reasons else "规则满足，等待人工确认"))
    # A bounded research tail keeps the whole-market pool visible without
    # silently making a newly discovered name a position-bearing candidate.
    research = [row for code, row in pool.items() if code not in configured]
    research.sort(key=lambda row: (row["lbc"], row["zttj"]["ct"], row["amount"]), reverse=True)
    for row in research[:3]:
        results.append(_board_result(row, previous_pool.get(row["code"]), board_quotes.get(row["code"]),
                                     previous_board_quotes.get(row["code"]), None, risk_reasons,
                                     held_pct, cap, snapshot))
    reentry_confirmed = not risk_reasons and any(
        stock.get("enabled", True) and not stock.get("ref_price")
        and _sector_count((snapshot.get("industries") or {}).get(stock.get("code"), ""),
                          snapshot.get("sector_counts") or {}) >= 4
        and _sector_count((snapshot.get("industries") or {}).get(stock.get("code"), ""),
                          snapshot.get("previous_sector_counts") or {}) >= 4
        for stock in plan.get("watchlist", []))
    return {"buy_allowed": not risk_reasons, "risk_reasons": risk_reasons,
            "reentry_confirmed": reentry_confirmed, "stocks": results,
            "note": "仅判定候选，不自动登记或下单；持仓只监控止损/兑现，不建议加仓。"}
