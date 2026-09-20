# -*- coding: utf-8 -*-
"""执行层骨架（P2）：默认 paper 模拟模式——只记录+回执，绝不真实下单。
真实自动交易需 miniQMT(xtquant)，接入前必须通过三重熔断：
  ① 单日已实现亏损达 -daily_max_loss_pct% → 全局停机
  ② 单票当日下单次数达 max_orders_per_stock → 拒单
  ③ 仅允许 plan.json watchlist 内标的（白名单）
paper 模式的意义：先用0成本验证"规则→信号→委托参数"全链路，
积累 trade_log.jsonl 数据，20笔后再评估是否值得上真实执行。
"""
import json, os, time
import datadir

DATA = datadir.data_dir()
LOG = os.path.join(DATA, "trade_log.jsonl")

def _plan():
    try:
        return json.load(open(os.path.join(DATA, "plan.json"), encoding="utf-8"))
    except Exception:
        return {}

def _today():
    return time.strftime("%Y-%m-%d")

def _read_log():
    rows = []
    if os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows

def circuit_break(code):
    """返回 None=可下单，否则为熔断原因（基于当日真实/paper 记录）"""
    cfg = _plan().get("execution", {})
    rows = [r for r in _read_log() if r.get("date") == _today()]
    n = sum(1 for r in rows if r.get("code") == code and r.get("side") == "buy")
    if n >= int(cfg.get("max_orders_per_stock", 3)):
        return "熔断: 单票当日下单已达%d次" % n
    capital = float(_plan().get("总资金", 100000))
    loss = sum(r.get("pnl", 0) for r in rows)
    if loss <= -capital * float(cfg.get("daily_max_loss_pct", 2)) / 100.0:
        return "熔断: 单日亏损达-%s%%" % cfg.get("daily_max_loss_pct", 2)
    watch = [s.get("code") for s in _plan().get("watchlist", [])]
    if code not in watch:
        return "熔断: %s 不在白名单" % code
    return None

def execute_buy(code, name, price, shares):
    """paper 委托登记。返回 (record, note)；真实下单永远安全拒绝（未实现）。"""
    reason = circuit_break(code)
    if reason:
        return None, reason
    rec = {"ts": time.strftime("%F %T"), "date": _today(), "code": code, "name": name,
           "side": "buy", "price": round(price, 3), "shares": shares, "real": False, "pnl": 0}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec, "paper模拟登记（真实执行需miniQMT+显式开启，当前安全拒绝）"
