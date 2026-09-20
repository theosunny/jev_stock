#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股买点/平仓监控 -> 飞书提醒
用法: python3 monitor.py --mode test|once|auction|intraday|close
- test:      发一条测试消息，验证飞书链路
- once:      拉取当前快照打印（不推送，供 skill/人工查看）
- auction:   9:15-9:30 竞价概览推送
- intraday:  盘中规则检查（买入区/弱转强/止损/兑现），cron每5分钟调
- close:     收盘总结推送
规则与标的在 plan.json；战法: 陈小群(主线龙头分歧低吸) + 小鳄鱼(仓位管控/二板弱转强)
"""
import argparse, datetime as dt, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datadir, push, quotes

DATA = datadir.data_dir()
PLAN_PATH = os.path.join(DATA, "plan.json")
STATE_PATH = os.path.join(DATA, ".alert_state.json")
SESSIONS = ((dt.time(9, 30), dt.time(11, 30)), (dt.time(13, 0), dt.time(15, 0)))

def now():
    return dt.datetime.now()

def today():
    return now().strftime("%Y-%m-%d")

def in_session():
    t = now().time()
    return any(s <= t <= e for s, e in SESSIONS)

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

def load_plan():
    plan = load_json(PLAN_PATH, {"watchlist": []})
    return [s for s in plan.get("watchlist", []) if s.get("enabled")]

def alert_once(state, code, kind):
    """每票每类提醒每天只发一次。返回True=应当发送"""
    key = code + ":" + kind
    if state.get(key) == today():
        return False
    state[key] = today()
    return True

def fmt(x):
    return ("%.2f" % x) if x is not None else "-"

def eval_stock(s, q, ma5, state, alerts):
    code, name = s["code"], s["name"]
    ref = s.get("ref_price")

    # ---- 持仓监控（ref_price 存在时）：止损/兑现 ----
    if ref:
        stop = ref * (1 - s.get("stop_loss_pct", 6) / 100.0)
        if q["price"] <= stop and alert_once(state, code, "stop"):
            alerts.append(
                "**%s %s 止损触发**\n现价 %s 已跌破止损价 %s（买入参考 %.2f 的-%s%%）\n"
                "按纪律当日离场，不等反弹。策略：%s"
                % (name, code, fmt(q["price"]), fmt(stop), ref,
                   s.get("stop_loss_pct", 6), s["strategy"]))
        lu = q.get("limit_up")
        if lu and q["price"] >= lu * 0.998 and alert_once(state, code, "profit_limitup"):
            alerts.append(
                "**%s %s 涨停·一致加速兑现提醒**\n现价 %s 触及涨停 %s\n"
                "战法: 卖在一致——涨停/缩量加速是兑现点。策略：%s"
                % (name, code, fmt(q["price"]), fmt(lu), s["strategy"]))
        if q["high"] >= ref * 1.05 and q["price"] <= q["high"] * 0.97 and alert_once(state, code, "profit_fallback"):
            alerts.append(
                "**%s %s 冲高回落·兑现提醒**\n日内高点 %s 回落逾3%%至 %s\n"
                "按 plan 兑现规则减仓/清仓。策略：%s"
                % (name, code, fmt(q["high"]), fmt(q["price"]), s["strategy"]))
        return

    # ---- 买入观察：低吸型（5日线±band） ----
    if s.get("buy_anchor") == "ma5" and ma5:
        band = s.get("buy_band_pct", 1.0) / 100.0
        lo, hi = ma5 * (1 - band), ma5 * (1 + band)
        if lo <= q["price"] <= hi and alert_once(state, code, "buy_zone"):
            alerts.append(
                "**买入提醒 | %s %s**\n现价 %s 进入低吸区 %s-%s（MA5≈%s）\n"
                "策略: %s | 建议仓位: %s成试错\n"
                "止损: 买入价-%s%% | 兑现: 涨停或冲高回落>3%%\n"
                "注意: 若高开>5%%或放量大跌破位则放弃"
                % (name, code, fmt(q["price"]), fmt(lo), fmt(hi), fmt(ma5),
                   s["strategy"], s.get("buy_size_pct", 10), s.get("stop_loss_pct", 6)))

    # ---- 买入观察：弱转强确认型 ----
    c = s.get("confirm")
    if c:
        op = (q["open"] / q["prev_close"] - 1) * 100
        hh, mm = [int(x) for x in c.get("after_time", "10:00").split(":")]
        after = now().time() >= dt.time(hh, mm)
        strong = q["price"] >= q["open"] * (1 + c.get("price_above_open_pct", 1.0) / 100.0)
        if q.get("vwap"):
            strong = strong and q["price"] >= q["vwap"]  # 站稳分时均价(VWAP)
        lo_p, hi_p = c["open_pct_range"]
        if lo_p <= op <= hi_p and after and strong and alert_once(state, code, "confirm"):
            alerts.append(
                "**弱转强确认 | %s %s**\n竞价高开 %.1f%%（要求 %s-%s%%），现价 %s 站稳开盘价上方\n"
                "策略: %s | 建议: 小仓位%s成试错，跌破分时均线/开盘价放弃\n"
                "止损: -%s%%"
                % (name, code, op, lo_p, hi_p, fmt(q["price"]),
                   s["strategy"], s.get("buy_size_pct", 10), s.get("stop_loss_pct", 6)))

def snapshot():
    plan = load_plan()
    codes = [s["code"] for s in plan]
    if not codes:
        return None, None, []
    qs = quotes.fetch_quotes(codes)
    ma5s = {}
    for s in plan:
        try:
            ma5s[s["code"]] = quotes.fetch_ma5(s["code"])
        except Exception:
            ma5s[s["code"]] = None
    return plan, qs, ma5s

def mode_once():
    plan, qs, ma5s = snapshot()
    if not plan:
        print("watchlist 为空")
        return
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            print("%s: 行情获取失败" % s["code"])
            continue
        print("%s %s | 现价 %.2f (%.2f%%) 开 %.2f 高 %.2f 低 %.2f | MA5 %s | 行情时间 %s | %s"
              % (s["name"], s["code"], q["price"], q["pct"], q["open"], q["high"], q["low"],
                 fmt(ma5s.get(s["code"])), q["time"], s["strategy"]))

def push_or_log(msg):
    if os.environ.get("NO_PUSH"):
        print("[NO_PUSH]\n" + msg)
        return True
    ok, detail = push.send_markdown(msg)
    print(("已推送(%s)" % detail) if ok else ("推送失败: %s" % detail))
    return ok

def mode_test():
    msg = ("**stock-signal 链路测试**\n如果你看到这条消息，说明 A股提醒 -> 飞书 推送正常。\n"
           "计划标的: " + ", ".join(s["name"] for s in load_plan()) + "\n时间: " + now().strftime("%F %T"))
    push_or_log(msg)

def mode_auction():
    if not (dt.time(9, 14) <= now().time() <= dt.time(9, 30)):
        return
    state = load_json(STATE_PATH, {})
    if not alert_once(state, "market", "auction"):
        return
    plan, qs, _ = snapshot()
    if not plan or not qs:
        save_json(STATE_PATH, state)
        return
    lines = ["**竞价概览 %s**" % today()]
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            continue
        op = (q["open"] / q["prev_close"] - 1) * 100 if q["prev_close"] else 0
        tag = ""
        c = s.get("confirm")
        if c:
            lo_p, hi_p = c["open_pct_range"]
            tag = " 弱转强预备✓" if lo_p <= op <= hi_p else " 高开区间外，大概率放弃"
        lines.append("- %s %s: 高开 %+.1f%%%s 现价 %s" % (s["name"], s["code"], op, tag, fmt(q["price"])))
    lines.append("提示: 高潮期若一致大幅高开，按战法只兑现不追。")
    save_json(STATE_PATH, state)
    push_or_log("\n".join(lines))

def mode_intraday():
    plan, qs, ma5s = snapshot()
    if not plan or not qs:
        return
    q0 = qs.get(plan[0]["code"])
    if not q0 or q0["time"][:8] != now().strftime("%Y%m%d") or not in_session():
        return  # 非交易日/非交易时段/无行情
    state = load_json(STATE_PATH, {})
    alerts = []
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            continue
        eval_stock(s, q, ma5s.get(s["code"]), state, alerts)
    if alerts:
        save_json(STATE_PATH, state)
        push_or_log("\n".join(alerts) + "\n\n_" + now().strftime("%F %T") + "_")

def mode_close():
    state = load_json(STATE_PATH, {})
    if not alert_once(state, "market", "close"):
        return
    plan, qs, ma5s = snapshot()
    if not plan or not qs:
        save_json(STATE_PATH, state)
        return
    q0 = qs.get(plan[0]["code"])
    if not q0 or q0["time"][:8] != now().strftime("%Y%m%d"):
        save_json(STATE_PATH, state)
        return
    lines = ["**收盘总结 %s**" % today()]
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            continue
        ma5 = ma5s.get(s["code"])
        pos = "持仓 ref=%.2f" % s["ref_price"] if s.get("ref_price") else "观察中(未持仓)"
        lines.append("- %s %s: 收 %.2f (%+.2f%%) MA5 %s | %s | %s"
                     % (s["name"], s["code"], q["price"], q["pct"], fmt(ma5), pos, s["strategy"]))
    lines.append("买入后请把 plan.json 该票 ref_price 改为实际买入价，止损/兑现监控才会生效。")
    save_json(STATE_PATH, state)
    push_or_log("\n".join(lines))

def mode_buy(code, price):
    plan = load_json(PLAN_PATH, None) or {"watchlist": []}
    for s in plan.get("watchlist", []):
        if s["code"] == code:
            break
    else:
        sys.exit("plan.json 中没有 " + code)
    s["ref_price"] = price
    save_json(PLAN_PATH, plan)
    stop = price * (1 - s.get("stop_loss_pct", 6) / 100.0)
    msg = ("**持仓登记 | %s %s**\n买入价 %.2f 已写入 plan.json\n"
           "止损价 %.2f (-%s%%，触发即提醒) | 兑现: 涨停或冲高回落>3%%\n策略: %s"
           % (s["name"], code, price, stop, s.get("stop_loss_pct", 6), s["strategy"]))
    push_or_log(msg)

def mode_sell(code):
    plan = load_json(PLAN_PATH, None) or {"watchlist": []}
    for s in plan.get("watchlist", []):
        if s["code"] == code:
            s["ref_price"] = None
            save_json(PLAN_PATH, plan)
            print("已清除 %s 的持仓登记（回到观察模式）" % code)
            return
    sys.exit("plan.json 中没有 " + code)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True,
                    choices=["test", "once", "auction", "intraday", "close", "buy", "sell"])
    ap.add_argument("--code", help="buy/sell 用: 如 sz002185")
    ap.add_argument("--price", type=float, help="buy 用: 实际买入价")
    a = ap.parse_args()
    if a.mode == "buy":
        if not a.code or not a.price:
            sys.exit("用法: --mode buy --code sz002185 --price 17.80")
        mode_buy(a.code, a.price)
    elif a.mode == "sell":
        if not a.code:
            sys.exit("用法: --mode sell --code sz002185")
        mode_sell(a.code)
    else:
        {"test": mode_test, "once": mode_once, "auction": mode_auction,
         "intraday": mode_intraday, "close": mode_close}[a.mode]()
