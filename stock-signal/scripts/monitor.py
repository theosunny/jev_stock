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
import datadir, execution, market, push, quotes

DATA = datadir.data_dir()
PLAN_PATH = os.path.join(DATA, "plan.json")
STATE_PATH = os.path.join(DATA, ".alert_state.json")
SESSIONS = ((dt.time(9, 30), dt.time(11, 30)), (dt.time(13, 0), dt.time(15, 0)))
FORCE = False  # --force 时忽略时段/交易日限制(自测)

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
    try:
        with open(PLAN_PATH, encoding="utf-8") as f:
            plan = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        try:
            if not os.environ.get("NO_PUSH"):
                push.send_markdown("**\u26a0\ufe0f plan.json 损坏**\n%s\n监控已跳过本次执行，请修复后再交易" % e)
        except Exception:
            pass
        raise SystemExit(1)
    return [s for s in plan.get("watchlist", []) if s.get("enabled")]

def plan_capital():
    try:
        return float(load_json(PLAN_PATH, {}).get("总资金", 100000))
    except Exception:
        return 100000.0

def shares_for(price, pct):
    """按预算与价格算A股整数股（100股一手）"""
    if not price:
        return None
    budget = plan_capital() * pct / 100.0
    return int(budget // (price * 100)) * 100

def vol_ratio_threshold():
    """加仓量比时间归一: 10:00需昨日量20% -> 15:00需100%"""
    t = now()
    mins = max(0, min(330, (t.hour * 60 + t.minute) - (9 * 60 + 30)))
    return 0.2 + (mins / 330.0) * 0.8

def position_summary():
    plan = load_json(PLAN_PATH, {})
    cap = plan.get("总仓位上限pct", 30)
    held = [(s["name"], s.get("buy_size_pct", 10)) for s in plan.get("watchlist", []) if s.get("ref_price")]
    used = sum(x[1] for x in held)
    names = "\u3001".join(n for n, _ in held) or "空仓"
    warn = " \u26a0\ufe0f超上限" if used > cap else ""
    m = plan_capital() / 10000.0
    return "仓位: %s | 已用%d%%(%.1f万)/上限%d%%(%.1f万)%s" % (names, used, used * m / 100.0, cap, cap * m / 100.0, warn)

def buy_points_lines(plan, qs, ma5s):
    lines = []
    for s in plan:
        if s.get("ref_price") or not s.get("enabled", True):
            continue
        if s.get("buy_anchor") == "ma5" and ma5s.get(s["code"]):
            ma5 = ma5s[s["code"]]
            band = s.get("buy_band_pct", 1.0) / 100.0
            lo, hi = ma5 * (1 - band), ma5 * (1 + band)
            pct = s.get("buy_size_pct", 10)
            sh = shares_for((lo + hi) / 2.0, pct)
            lines.append("- %s 低吸区 %.2f-%.2f (MA5 %.2f) | %.1f成≈%d股/约%.0f元" % (
                s["name"], lo, hi, ma5, pct / 10.0, sh or 0,
                (sh or 0) * (lo + hi) / 2.0))
        elif s.get("confirm"):
            lo_p, hi_p = s["confirm"]["open_pct_range"]
            lines.append("- %s 弱转强: 高开%d-%d%%后站稳VWAP | %.1f成预算≈%.0f元" % (
                s["name"], lo_p, hi_p, s.get("buy_size_pct", 10) / 10.0,
                plan_capital() * s.get("buy_size_pct", 10) / 100.0))
    return lines

def market_lines():
    out = []
    d, lab = None, ""
    if now().time() < dt.time(10, 0):  # 开盘首小时当日池数据不全，用昨日完整数据
        d = market.prev_trading_date(now().strftime("%Y%m%d"))
        lab = "昨日"
    try:
        t = market.emotion_line(d, lab)
        if t:
            out.append(t)
    except Exception:
        pass
    try:
        t = market.heat_line(d, label=lab)
        if t:
            out.append(t)
    except Exception:
        pass
    for kind, name in ((2, "行业"), (3, "概念")):
        try:
            t = market.sector_line(3, kind, name)
            if t:
                out.append(t)
        except Exception:
            pass
    return out

def alert_once(state, code, kind):
    """每票每类提醒每天只发一次。返回True=应当发送"""
    key = code + ":" + kind
    if state.get(key) == today():
        return False
    state[key] = today()
    return True

def fmt(x):
    return ("%.2f" % x) if x is not None else "-"

def eval_stock(s, q, ma5, state, alerts, buy_ok=True):
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
        fell = q["high"] >= ref * 1.05 and q["price"] <= q["high"] * 0.97
        if fell and alert_once(state, code, "profit_fallback"):
            alerts.append(
                "**%s %s 冲高回落·兑现提醒**\n日内高点 %s 回落逾3%%至 %s\n"
                "按 plan 兑现规则减仓/清仓。策略：%s"
                % (name, code, fmt(q["high"]), fmt(q["price"]), s["strategy"]))
        elif not fell and q["price"] >= ref * 1.05 and not (lu and q["price"] >= lu * 0.998):
            prev_vol = 0.0
            try:
                prev_vol = quotes.fetch_prev_volume(code) or 0.0
            except Exception:
                pass
            thr = vol_ratio_threshold()
            vol_ok = prev_vol <= 0 or q.get("volume", 0.0) >= prev_vol * thr
            if vol_ok and alert_once(state, code, "add_pos"):
                add_pct = s.get("add_size_pct", 5)
                sh = shares_for(q["price"], add_pct)
                alerts.append(
                    "**加仓提醒 | %s %s**\n现价 %s 浮盈 %.1f%% 且有量(需达昨日量%d%%)\n"
                    "可金字塔加 %.1f成≈%d股(约%d元)，加后单票≤2成\n"
                    "注意: 一致加速(涨停/一字)是兑现点不加仓；加仓后 --mode buy --price 新均价 --size 更新登记"
                    % (name, code, fmt(q["price"]), (q["price"] / ref - 1) * 100, int(thr * 100),
                       add_pct / 10.0, sh or 0, int((sh or 0) * q["price"])))
        return

    if (not buy_ok or not s.get("buy_enabled", True)) and not s.get("ref_price"):
        return  # 闸门: 退潮期(全局)或个股buy_enabled=false时, 暂停买入类提醒; 持仓监控照常

    # ---- 板块联动过滤（主线退潮则降级为观察） ----
    ind, zt_cnt, sec_tag = "", None, ""
    try:
        ind = market.stock_industry(code).get("industry") or ""
    except Exception:
        pass
    if ind:
        try:
            zt_cnt = market.sector_zt_count(ind)
        except Exception:
            zt_cnt = None
    if zt_cnt == 0:
        sec_tag = " \u26a0\ufe0f板块[%s]今日0涨停·主线退潮，建议观望" % ind
    elif zt_cnt:
        sec_tag = "（板块[%s]涨停%d家）" % (ind, zt_cnt)

    # ---- 盘中辅助信号：接近低吸区 / VWAP收复 ----
    if s.get("buy_anchor") == "ma5" and ma5 and not s.get("ref_price"):
        band0 = s.get("buy_band_pct", 1.0) / 100.0
        lo0, hi0 = ma5 * (1 - band0), ma5 * (1 + band0)
        if hi0 < q["price"] <= hi0 * 1.015 and alert_once(state, code, "approach"):
            alerts.append("**接近低吸区 | %s %s**\n现价 %s 距低吸区上沿 %.2f 仅 %.1f%%\n准备信号(非买入): 跌入 %.2f-%.2f 才触发正式买入提醒"
                            % (name, code, fmt(q["price"]), hi0,
                               (q["price"] / hi0 - 1) * 100, lo0, hi0))
    if not s.get("ref_price") and q.get("vwap") and now().time() >= dt.time(10, 0):
        bk = "_vwap_below:" + code
        if q["price"] < q["vwap"] * 0.995:
            state[bk] = True
        elif state.get(bk) and q["price"] >= q["vwap"] * 1.005:
            state[bk] = False
            if alert_once(state, code, "vwap_reclaim"):
                alerts.append("**VWAP收复 | %s %s**\n现价 %s 站回分时均价 %s 之上\n盘中企稳信号——量能请自行确认；低吸型仍以低吸区为准，弱转强型此为确认信号之一"
                                % (name, code, fmt(q["price"]), fmt(q["vwap"])))

    # ---- 买入观察：低吸型（5日线±band） ----
    if s.get("buy_anchor") == "ma5" and ma5:
        band = s.get("buy_band_pct", 1.0) / 100.0
        lo, hi = ma5 * (1 - band), ma5 * (1 + band)
        if lo <= q["price"] <= hi and alert_once(state, code, "buy_zone"):
            alerts.append(
                "**买入提醒 | %s %s**\n现价 %s 进入低吸区 %s-%s（MA5≈%s）\n"
                "策略: %s%s | 建议仓位: %.1f成试错≈%s股(约%s元)\n"
                "止损: 买入价-%s%%(-约%s元) | 兑现: 涨停或冲高回落>3%%\n"
                "注意: 若高开>5%%或放量大跌破位则放弃"
                % (name, code, fmt(q["price"]), fmt(lo), fmt(hi), fmt(ma5),
                   s["strategy"], sec_tag, s.get("buy_size_pct", 10) / 10.0,
                   shares_for((lo + hi) / 2.0, s.get("buy_size_pct", 10)) or 0,
                   int((shares_for((lo + hi) / 2.0, s.get("buy_size_pct", 10)) or 0) * (lo + hi) / 2.0),
                   s.get("stop_loss_pct", 6),
                   int((shares_for((lo + hi) / 2.0, s.get("buy_size_pct", 10)) or 0) * (lo + hi) / 2.0 * s.get("stop_loss_pct", 6) / 100.0)))
            try:
                rec, _why = execution.execute_buy(code, name, q["price"],
                                                 shares_for((lo + hi) / 2.0, s.get("buy_size_pct", 10)) or 0)
                if rec:
                    alerts.append("[paper] 模拟委托已登记 %d股@%s（真实执行需miniQMT+熔断验证）"
                                  % (rec["shares"], fmt(rec["price"])))
            except Exception:
                pass

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
        high_ok = lo_p <= op <= hi_p and after and strong
        low_ok = False
        lw = c.get("low_open")
        if lw and after and q.get("vwap"):
            llo, lhi = lw["open_pct_range"]
            low_ok = (llo <= op <= lhi and q["price"] >= q["vwap"]
                      and q["price"] >= q["prev_close"])  # 低开回升翻红站稳VWAP
        if (high_ok or low_ok) and alert_once(state, code, "confirm"):
            how = ("竞价高开 %.1f%%（要求 %s-%s%%）" % (op, lo_p, hi_p)) if high_ok else ("低开 %.1f%% 回升翻红" % op)
            alerts.append(
                "**弱转强确认 | %s %s**\n%s，现价 %s 站稳VWAP %s\n"
                "策略: %s%s | 建议: 小仓位%.1f成≈%s股(约%s元)，跌破分时均线/开盘价放弃\n"
                "止损: -%s%%"
                % (name, code, how, fmt(q["price"]), fmt(q.get("vwap")),
                   s["strategy"], sec_tag, s.get("buy_size_pct", 10) / 10.0,
                   shares_for(q["price"], s.get("buy_size_pct", 10)) or 0,
                   int((shares_for(q["price"], s.get("buy_size_pct", 10)) or 0) * q["price"]),
                   s.get("stop_loss_pct", 6)))
            try:
                rec, _why = execution.execute_buy(code, name, q["price"],
                                                 shares_for(q["price"], s.get("buy_size_pct", 10)) or 0)
                if rec:
                    alerts.append("[paper] 模拟委托已登记 %d股@%s（真实执行需miniQMT+熔断验证）"
                                  % (rec["shares"], fmt(rec["price"])))
            except Exception:
                pass
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
    for line in market_lines():
        print(line)
    inds = {}
    for s in plan:
        try:
            inds[s["code"]] = market.stock_industry(s["code"]).get("industry") or ""
        except Exception:
            inds[s["code"]] = ""
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            print("%s: 行情获取失败" % s["code"])
            continue
        print("%s %s[%s] | 现价 %.2f (%.2f%%) 开 %.2f VWAP %s | MA5 %s | %s | %s"
              % (s["name"], s["code"], inds[s["code"]], q["price"], q["pct"], q["open"],
                 fmt(q.get("vwap")), fmt(ma5s.get(s["code"])),
                 ("持仓@%.2f" % s["ref_price"]) if s.get("ref_price") else "观察", s["strategy"]))
    print(position_summary())
    pts = buy_points_lines(plan, qs, ma5s)
    if pts:
        print("买点: " + "; ".join(x.lstrip("- ") for x in pts))

def push_or_log(msg):
    if os.environ.get("NO_PUSH"):
        print("[NO_PUSH]\n" + msg)
        return True
    ok, detail = push.send_markdown(msg)
    print(("[%s] " % now().strftime("%F %T")) +
          (("已推送(%s)" % detail) if ok else ("推送失败: %s" % detail)))
    return ok

def check_risk_off(state, alerts):
    """大盘退潮预警: 炸板率≥30% / 高度板较前日降≥2 / 跌停≥20 → 减仓提醒（仅持仓时调用）"""
    try:
        em = market.emotion()
        if "error" in em:
            return
        prev_max = 0
        prev_d = market.prev_trading_date(em["date"])
        if prev_d:
            prev_max = market.emotion(prev_d).get("max_lb", 0)
        reasons = []
        if em["zb_rate"] >= 30:
            reasons.append("炸板率%.1f%%≥30%%" % em["zb_rate"])
        if prev_max and em["max_lb"] <= prev_max - 2:
            reasons.append("最高连板%d→%d" % (prev_max, em["max_lb"]))
        if em["dt"] >= 20:
            reasons.append("跌停%d家" % em["dt"])
        if reasons and alert_once(state, "market", "risk_off"):
            alerts.append("**\u26a0\ufe0f 大盘退潮预警**\n%s\n按纪律: 持仓全部减半或清仓、停止新开仓，等情绪企稳"
                          % "；".join(reasons))
    except Exception:
        pass

def check_sector_eruption(state, alerts):
    """板块爆发: 行业涨停家数较上次快照新增>=3家 -> 新主线候选提醒(只记录不追)"""
    if now().time() < dt.time(9, 40):  # 避开开盘涨停池自然爬坡期
        return
    try:
        pool = market.zt_pool()
        day = now().strftime("%Y%m%d")
        cur = {}
        for p in pool:
            ind = p.get("hybk")
            if ind:
                cur.setdefault(ind, []).append((p.get("n", ""), int(p.get("lbc", 1))))
    except Exception:
        return
    if len(pool) < 5 or not cur:  # 空池/半加载池视为失败，不动基线
        return
    snap = state.get("_sector_snap")
    if not snap or snap.get("date") != day:  # 每日首跑只建基线
        state["_sector_snap"] = {"date": day,
                                 "counts": {k: len(v) for k, v in cur.items()},
                                 "names": {k: [n for n, _ in v] for k, v in cur.items()}}
        return
    prev, prev_names = snap.get("counts", {}), snap.get("names", {})
    for ind, lst in cur.items():
        delta = len(lst) - prev.get(ind, 0)
        if delta >= 3 and alert_once(state, "erupt:" + ind, "x"):
            firsts = [n for n, lb in lst if lb == 1][:4]
            alerts.append("**\U0001F525 板块爆发 | %s**\n涨停家数 %d\u2192%d (+%d)\n首板: %s\n定位: 新主线候选——今日只记录观察，次日弱转强确认再评估(不追当日首板)"
                            % (ind, prev.get(ind, 0), len(lst), delta, "/".join(firsts or [n for n, _ in lst][:4])))
    state["_sector_snap"] = {"date": day,
                             "counts": {k: len(v) for k, v in cur.items()},
                             "names": {k: [n for n, _ in v] for k, v in cur.items()}}

def mode_heartbeat():
    """9:25 心跳: 你收不到这条=系统挂了（Mac睡眠/网络/cron问题）"""
    state = load_json(STATE_PATH, {})
    if not alert_once(state, "market", "heartbeat"):
        return
    plan = load_plan()
    held = [s for s in plan if s.get("ref_price")]
    lines = ["**\U0001F49B 监控在线 %s**" % today(),
             "watchlist %d 只，持仓 %d 只" % (len(plan), len(held)),
             position_summary()]
    if plan:
        try:
            _, qs, ma5s = snapshot()
            pts = buy_points_lines(plan, qs, ma5s)
            if pts:
                lines += pts
        except Exception:
            pass
    save_json(STATE_PATH, state)
    push_or_log("\n".join(lines))


def mode_test():
    msg = ("**stock-signal 链路测试**\n如果你看到这条消息，说明 A股提醒 -> 飞书 推送正常。\n"
           "计划标的: " + ", ".join(s["name"] for s in load_plan()) + "\n时间: " + now().strftime("%F %T"))
    push_or_log(msg)

def mode_auction():
    if not FORCE and not (dt.time(9, 14) <= now().time() <= dt.time(9, 30)):
        return
    state = load_json(STATE_PATH, {})
    if not alert_once(state, "market", "auction"):
        return
    plan, qs, ma5s = snapshot()
    if not plan or not qs:
        save_json(STATE_PATH, state)
        return
    lines = ["**竞价概览 %s**" % today()]
    lines += market_lines()
    try:
        t = market.overnight_line()
        if t:
            lines.append(t)
    except Exception:
        pass
    ann_parts = []
    for s in plan:
        try:
            a = market.announcements(s["code"])
            if a.get("risk"):
                ann_parts.append("%s \u26a0\ufe0f%s" % (s["name"], ";".join(a["risk"][:2])))
            elif a.get("good"):
                ann_parts.append("%s \u2726%s" % (s["name"], ";".join(a["good"][:1])))
        except Exception:
            pass
    if ann_parts:
        lines.append("公告: " + " | ".join(ann_parts))
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            continue
        op = (q["open"] / q["prev_close"] - 1) * 100 if q["prev_close"] else 0
        tag = ""
        c = s.get("confirm")
        if c:
            lo_p, hi_p = c["open_pct_range"]
            tag = " 弱转强预备" if lo_p <= op <= hi_p else " 高开区间外，大概率放弃"
        lines.append("- %s %s: 高开 %+.1f%%%s 现价 %s" % (s["name"], s["code"], op, tag, fmt(q["price"])))
    pts = buy_points_lines(plan, qs, ma5s)
    if pts:
        lines.append("**今日买点**")
        lines += pts
    lines.append(position_summary())
    try:
        ebb, ebb_why = market.is_ebb()
        if ebb:
            lines.append("退潮闸门: 买入提醒今日暂停(%s)，只观察不新开仓" % ebb_why)
    except Exception:
        pass
    lines.append("提示: 高潮期若一致大幅高开，按战法只兑现不追。")
    save_json(STATE_PATH, state)
    push_or_log("\n".join(lines))

def mode_intraday():
    plan, qs, ma5s = snapshot()
    if not plan or not qs:
        return
    q0 = qs.get(plan[0]["code"])
    if not FORCE and (not q0 or q0["time"][:8] != now().strftime("%Y%m%d") or not in_session()):
        return  # 非交易日/非交易时段/无行情
    state = load_json(STATE_PATH, {})
    alerts = []
    if any(s.get("ref_price") for s in plan):
        check_risk_off(state, alerts)
    ebb, ebb_why = market.is_ebb()
    buy_ok = not ebb
    if ebb and alert_once(state, "market", "ebb_note"):
        alerts.append("**退潮闸门生效**\n%s\n买入类提醒已自动暂停(低吸区/弱转强/接近预警)；恢复条件: 炸板率<25%%且高度板企稳回升。持仓监控(止损/兑现)不受影响" % ebb_why)
    check_sector_eruption(state, alerts)
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            continue
        eval_stock(s, q, ma5s.get(s["code"]), state, alerts, buy_ok=buy_ok)
    save_json(STATE_PATH, state)  # 快照/标志需持久化，每次都保存
    if alerts:
        push_or_log("\n".join(alerts) + "\n" + position_summary() + "\n\n_" + now().strftime("%F %T") + "_")

def mode_close():
    state = load_json(STATE_PATH, {})
    if not alert_once(state, "market", "close"):
        return
    plan, qs, ma5s = snapshot()
    if not plan or not qs:
        save_json(STATE_PATH, state)
        return
    q0 = qs.get(plan[0]["code"])
    if not FORCE and (not q0 or q0["time"][:8] != now().strftime("%Y%m%d")):
        save_json(STATE_PATH, state)
        return
    lines = ["**收盘总结 %s**" % today()]
    lines += market_lines()
    for s in plan:
        q = qs.get(s["code"])
        if not q:
            continue
        ma5 = ma5s.get(s["code"])
        pos = "持仓@%.2f" % s["ref_price"] if s.get("ref_price") else "观察中"
        lines.append("- %s %s: 收 %.2f (%+.2f%%) MA5 %s | %s | %s"
                     % (s["name"], s["code"], q["price"], q["pct"], fmt(ma5), pos, s["strategy"]))
    lines.append(position_summary())
    pts = buy_points_lines(plan, qs, ma5s)
    if pts:
        lines.append("**次日买点**")
        lines += pts
    lines.append("买入后: python3 monitor.py --mode buy --code 代码 --price 实际价")
    save_json(STATE_PATH, state)
    push_or_log("\n".join(lines))

def mode_buy(code, price=None, size=None):
    plan = load_json(PLAN_PATH, None) or {"watchlist": []}
    for s in plan.get("watchlist", []):
        if s["code"] == code:
            break
    else:
        sys.exit("plan.json 中没有 " + code)
    auto_note = ""
    if price is None:  # 免价格登记: 取实时价
        q = quotes.fetch_quotes([code]).get(code)
        if not q:
            sys.exit("未提供价格且实时行情获取失败")
        price = q["price"]
        auto_note = "\n(按实时价%.2f登记, 实际成交价不同请 --price 修正)" % price
    s["ref_price"] = price
    if size:
        s["buy_size_pct"] = size
    save_json(PLAN_PATH, plan)
    stop = price * (1 - s.get("stop_loss_pct", 6) / 100.0)
    msg = ("**持仓登记 | %s %s**\n买入价 %.2f 已写入 plan.json\n"
           "止损价 %.2f (-%s%%，触发即提醒) | 兑现: 涨停或冲高回落>3%%\n策略: %s"
           % (s["name"], code, price, stop, s.get("stop_loss_pct", 6), s["strategy"]))
    push_or_log(msg + auto_note + "\n" + position_summary())

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
                    choices=["test", "once", "auction", "intraday", "close", "buy", "sell", "heartbeat"])
    ap.add_argument("--code", help="buy/sell 用: 如 sz002185")
    ap.add_argument("--price", type=float, help="buy 用: 实际买入价")
    ap.add_argument("--size", type=int, help="buy 用: 更新该票仓位百分比(如5=0.5成)")
    ap.add_argument("--force", action="store_true", help="忽略时段/交易日限制(自测)")
    a = ap.parse_args()
    if a.force:
        FORCE = True
    if a.mode == "buy":
        if not a.code:
            sys.exit("用法: --mode buy --code sz002185 [--price 不填取实时价]")
        mode_buy(a.code, a.price, a.size)
    elif a.mode == "sell":
        if not a.code:
            sys.exit("用法: --mode sell --code sz002185")
        mode_sell(a.code)
    else:
        {"test": mode_test, "once": mode_once, "auction": mode_auction,
         "intraday": mode_intraday, "close": mode_close,
         "heartbeat": mode_heartbeat}[a.mode]()
