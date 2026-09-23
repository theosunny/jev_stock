"""Independent, conservative paper ledger for recommendation tracking.

This module deliberately has no dependency on the live execution or plan paths.
It records recommendations immutably and simulates only paper limit orders.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
import uuid
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CAPITAL = Decimal("100000")
_PER_STOCK_CAP = Decimal("20000")
_LOT = 100
_QUOTE_MAX_AGE = dt.timedelta(seconds=180)
_REQUIRED = {
    "id", "report_id", "code", "name", "recommended_at", "price", "action",
    "reason", "entry_condition", "invalidation", "strategy_version", "expires_at", "budget",
}
_OPTIONAL_ENTRY_TYPE = "entry_type"
_BOARD_ENTRY_TYPES = {"排板候选", "回封候选"}


def _time(value: dt.datetime | str) -> dt.datetime:
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("time must be ISO-8601") from exc
    if not isinstance(value, dt.datetime):
        raise ValueError("time must be a datetime or ISO-8601 string")
    # All persisted and compared timestamps are Shanghai local, timezone-naive.
    if value.tzinfo is not None:
        value = value.astimezone(_SHANGHAI).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _amount(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{field} must be positive")
    return amount


def _fee(gross: Decimal) -> Decimal:
    """Paper-only commission assumption: 0.03%, with a 5 CNY minimum."""
    return max(Decimal("5"), (gross * Decimal("0.0003")).quantize(Decimal("0.01")))


def _in_session(value: dt.datetime) -> bool:
    if value.weekday() >= 5:
        return False
    clock = value.time()
    return dt.time(9, 30) <= clock <= dt.time(11, 30) or dt.time(13, 0) <= clock <= dt.time(15, 0)


def _formal_action(record: dict) -> tuple[bool, str | None]:
    if record.get(_OPTIONAL_ENTRY_TYPE) in _BOARD_ENTRY_TYPES:
        # A coarse snapshot cannot prove board-queue or board-reseal execution.
        return False, "queue_snapshot_not_allowed"
    action = str(record["action"]).strip()
    if action in {"buy", "买入", "买入候选", "低吸", "弱转强", "回封候选"}:
        return True, None
    if action in {"排板", "排板候选"}:
        # A queued board cannot be inferred from a coarse quote or recommendation text.
        return False, "queue_snapshot_not_allowed"
    return False, "not_formal_buy"


class Ledger:
    """Local additive recommendation archive and independent paper simulator."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Tighten only the ledger's own directory, never an ancestor data directory.
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        os.chmod(self.path, 0o600)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, code TEXT NOT NULL,
                name TEXT NOT NULL, recommended_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                price TEXT NOT NULL, budget TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL UNIQUE,
                code TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
                limit_price TEXT NOT NULL, quantity INTEGER NOT NULL, reserved_cash TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT,
                FOREIGN KEY(recommendation_id) REFERENCES recommendations(id)
            );
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, recommendation_id TEXT,
                code TEXT NOT NULL, name TEXT NOT NULL, side TEXT NOT NULL, filled_at TEXT NOT NULL,
                price TEXT NOT NULL, quantity INTEGER NOT NULL, fee TEXT NOT NULL
            );
        """)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def ingest(self, recommendations: list[dict]) -> dict:
        inserted = existing = 0
        with self._lock, self._db:
            for incoming in recommendations:
                record = self._normalize_recommendation(incoming)
                payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                row = self._db.execute("SELECT payload FROM recommendations WHERE id = ?", (record["id"],)).fetchone()
                if row:
                    if row["payload"] != payload:
                        raise ValueError(f"recommendation id {record['id']!r} is immutable")
                    existing += 1
                    continue
                self._db.execute(
                    "INSERT INTO recommendations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (record["id"], payload, record["code"], record["name"] or record["code"], record["recommended_at"],
                     record["expires_at"], record["price"] or "", record["budget"] or ""),
                )
                inserted += 1
        return {"inserted": inserted, "existing": existing}

    def create_order(self, recommendation_id: str, now: dt.datetime | None = None) -> dict:
        now = _time(now or dt.datetime.now(_SHANGHAI))
        with self._lock:
            with self._db:
                self._expire_due(now)
            rec = self._recommendation(recommendation_id)
            if rec is None:
                return {"status": "rejected", "reason": "unknown_recommendation", "recommendation_id": recommendation_id}
            old = self._db.execute("SELECT * FROM orders WHERE recommendation_id = ?", (recommendation_id,)).fetchone()
            if old:
                if old["status"] == "pending" and now >= _time(rec["expires_at"]):
                    with self._db:
                        self._expire_order(old["id"])
                    old = self._db.execute("SELECT * FROM orders WHERE recommendation_id = ?", (recommendation_id,)).fetchone()
                return self._order(old)
            if now < _time(rec["recommended_at"]):
                return {"status": "rejected", "reason": "not_yet_recommended", "recommendation_id": recommendation_id}
            if now >= _time(rec["expires_at"]):
                return {"status": "rejected", "reason": "expired", "recommendation_id": recommendation_id}
            if not _in_session(now):
                return {"status": "rejected", "reason": "outside_trading_session", "recommendation_id": recommendation_id}
            formal, reason = _formal_action(rec)
            if not formal:
                return {"status": "rejected", "reason": reason, "recommendation_id": recommendation_id}
            try:
                price, budget = _amount(rec["price"], "price"), _amount(rec["budget"], "budget")
            except ValueError:
                return {"status": "rejected", "reason": "invalid_order_terms", "recommendation_id": recommendation_id}
            allowed = min(budget, _PER_STOCK_CAP - self._stock_exposure(rec["code"]), self._available_cash())
            quantity = self._quantity_for(allowed, price)
            if quantity < _LOT:
                return {"status": "rejected", "reason": "insufficient_capital", "recommendation_id": recommendation_id}
            gross = price * quantity
            reserved = gross + _fee(gross)
            order = {
                "id": f"paper-{uuid.uuid4().hex}", "recommendation_id": recommendation_id,
                "code": rec["code"], "name": rec["name"] or rec["code"], "created_at": now.isoformat(),
                "limit_price": str(price), "quantity": quantity, "reserved_cash": str(reserved),
                "status": "pending", "reason": "awaiting_fresh_tradable_quote",
            }
            with self._db:
                self._db.execute("""INSERT INTO orders VALUES (:id, :recommendation_id, :code, :name, :created_at,
                                  :limit_price, :quantity, :reserved_cash, :status, :reason)""", order)
            return order

    def cancel_order(self, order_id: str, now: dt.datetime | None = None) -> dict:
        """Safely release a pending paper reservation; no external action occurs."""
        if now is not None:
            _time(now)
        with self._lock, self._db:
            row = self._db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if row is None:
                return {"status": "rejected", "reason": "unknown_order", "id": order_id}
            if row["status"] != "pending":
                return self._order(row)
            self._db.execute("UPDATE orders SET status = 'cancelled', reason = 'cancelled_by_user', reserved_cash = '0' WHERE id = ?", (order_id,))
            return self._order(self._db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())

    def process_quotes(self, quotes: list[dict], now: dt.datetime | None = None) -> dict:
        processing_time = _time(now or dt.datetime.now(_SHANGHAI))
        grouped: dict[str, list[dict]] = {}
        for quote in quotes:
            if isinstance(quote, dict) and quote.get("code"):
                grouped.setdefault(str(quote["code"]), []).append(quote)
        filled = 0
        with self._lock, self._db:
            for row in self._db.execute("SELECT * FROM orders WHERE status = 'pending'").fetchall():
                rec = self._recommendation(row["recommendation_id"])
                if processing_time >= _time(rec["expires_at"]):
                    self._expire_order(row["id"])
                    continue
                snapshots = grouped.get(row["code"], [])
                if len(snapshots) != 1:
                    if len(snapshots) > 1:
                        self._set_order_reason(row["id"], "ambiguous_quote")
                    continue
                quote = snapshots[0]
                try:
                    quote_time = _time(quote["time"])
                    price = _amount(quote["price"], "quote price")
                    upper = _amount(quote["limit_up"], "limit_up")
                    lower = _amount(quote["limit_down"], "limit_down")
                except (KeyError, ValueError):
                    self._set_order_reason(row["id"], "invalid_quote")
                    continue
                if quote_time > processing_time:
                    self._set_order_reason(row["id"], "future_quote")
                elif quote_time <= _time(row["created_at"]):
                    self._set_order_reason(row["id"], "stale_quote")
                elif processing_time - quote_time > _QUOTE_MAX_AGE:
                    self._set_order_reason(row["id"], "stale_quote")
                elif quote_time >= _time(rec["expires_at"]):
                    self._expire_order(row["id"])
                elif not _in_session(quote_time):
                    self._set_order_reason(row["id"], "outside_trading_session")
                elif _decimal_or_none(quote.get("volume")) is None:
                    self._set_order_reason(row["id"], "no_liquidity")
                elif price >= upper:
                    self._set_order_reason(row["id"], "at_limit_up")
                elif price < lower or price > _amount(row["limit_price"], "limit price"):
                    self._set_order_reason(row["id"], "outside_limit")
                else:
                    gross = price * row["quantity"]
                    fee = _fee(gross)
                    self._db.execute("UPDATE orders SET status = 'filled', reason = NULL, reserved_cash = '0' WHERE id = ?", (row["id"],))
                    self._db.execute("INSERT INTO fills (order_id, recommendation_id, code, name, side, filled_at, price, quantity, fee) VALUES (?, ?, ?, ?, 'buy', ?, ?, ?, ?)",
                                     (row["id"], row["recommendation_id"], row["code"], row["name"], quote_time.isoformat(), str(price), row["quantity"], str(fee)))
                    filled += 1
            pending = self._db.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0]
        return {"filled": filled, "pending": pending}

    def close_position(self, code: str, quote: dict, now: dt.datetime | None = None) -> dict:
        processing_time = _time(now or dt.datetime.now(_SHANGHAI))
        with self._lock:
            position = next((p for p in self._positions() if p["code"] == code and p["quantity"]), None)
            if position is None:
                return {"status": "rejected", "reason": "no_position", "code": code}
            if quote.get("code") != code:
                return {"status": "rejected", "reason": "quote_code_mismatch", "code": code}
            try:
                quote_time, price = _time(quote["time"]), _amount(quote["price"], "quote price")
                lower, upper = _amount(quote["limit_down"], "limit_down"), _amount(quote["limit_up"], "limit_up")
            except (KeyError, ValueError):
                return {"status": "rejected", "reason": "invalid_quote", "code": code}
            if quote_time > processing_time:
                return {"status": "unfilled", "reason": "future_quote", "code": code}
            if processing_time - quote_time > _QUOTE_MAX_AGE:
                return {"status": "unfilled", "reason": "stale_quote", "code": code}
            if not _in_session(quote_time):
                return {"status": "unfilled", "reason": "outside_trading_session", "code": code}
            if quote_time.date() <= _time(position["latest_buy_at"]).date():
                return {"status": "rejected", "reason": "t_plus_one", "code": code}
            if _decimal_or_none(quote.get("volume")) is None:
                return {"status": "unfilled", "reason": "no_liquidity", "code": code}
            if price <= lower:
                return {"status": "unfilled", "reason": "at_limit_down", "code": code}
            if price > upper:
                return {"status": "unfilled", "reason": "outside_limit", "code": code}
            gross = price * position["quantity"]
            fee = _fee(gross)
            with self._db:
                self._db.execute("INSERT INTO fills (order_id, recommendation_id, code, name, side, filled_at, price, quantity, fee) VALUES (NULL, NULL, ?, ?, 'sell', ?, ?, ?, ?)",
                                 (code, position["name"], quote_time.isoformat(), str(price), position["quantity"], str(fee)))
            return {"status": "filled", "code": code, "quantity": position["quantity"], "price": str(price), "fee": str(fee)}

    def summary(self, now: dt.datetime | None = None) -> dict:
        with self._lock:
            with self._db:
                self._expire_due(_time(now or dt.datetime.now(_SHANGHAI)))
            return self._summary()

    def _summary(self) -> dict:
        recommendations = [json.loads(row["payload"]) for row in self._db.execute("SELECT payload FROM recommendations ORDER BY recommended_at, id")]
        orders = [self._order(row) for row in self._db.execute("SELECT * FROM orders ORDER BY created_at, id")]
        positions = self._positions()
        fills = self._db.execute("SELECT side, price, quantity, fee FROM fills").fetchall()
        buys = sum((Decimal(f["price"]) * f["quantity"] + Decimal(f["fee"]) for f in fills if f["side"] == "buy"), Decimal())
        sells = sum((Decimal(f["price"]) * f["quantity"] - Decimal(f["fee"]) for f in fills if f["side"] == "sell"), Decimal())
        return {
            "recommendations": recommendations, "orders": orders,
            "positions": [self._public_position(position) for position in positions],
            # No mark-to-market or lot matching is claimed: this is not a historical backtest.
            "metrics": {"capital_denominator": str(_CAPITAL), "fill_count": len(fills), "unfilled_order_count": sum(o["status"] == "pending" for o in orders), "cash": str(_CAPITAL - buys + sells - self._pending_reservations()), "realized_pnl": None, "unrealized_pnl": None, "return_pct": None, "historical_backtest": False},
            "rules": {"mode": "independent_paper_simulation", "capital": str(_CAPITAL), "max_per_stock": str(_PER_STOCK_CAP), "lot_size": _LOT, "t_plus_one": True, "historical_backtest": "not_available"},
        }

    def _normalize_recommendation(self, record: dict) -> dict:
        allowed_key_sets = (_REQUIRED, _REQUIRED | {_OPTIONAL_ENTRY_TYPE})
        if not isinstance(record, dict) or set(record) not in allowed_key_sets:
            raise ValueError("recommendation must contain required contract fields and optional entry_type")
        normalized = dict(record)
        for key in {"id", "report_id", "code", "recommended_at", "action", "expires_at"}:
            if not isinstance(normalized[key], str) or not normalized[key].strip():
                raise ValueError(f"{key} must be a non-empty string")
        for key in {"name", "reason", "entry_condition", "invalidation", "strategy_version"}:
            if normalized[key] is not None and not isinstance(normalized[key], str):
                raise ValueError(f"{key} must be a string or null")
        if _OPTIONAL_ENTRY_TYPE in normalized and normalized[_OPTIONAL_ENTRY_TYPE] is not None and not isinstance(normalized[_OPTIONAL_ENTRY_TYPE], str):
            raise ValueError("entry_type must be a string or null")
        normalized["recommended_at"] = _time(normalized["recommended_at"]).isoformat()
        normalized["expires_at"] = _time(normalized["expires_at"]).isoformat()
        if _time(normalized["expires_at"]) <= _time(normalized["recommended_at"]):
            raise ValueError("expires_at must be later than recommended_at")
        normalized["price"] = None if normalized["price"] is None else str(_finite_decimal(normalized["price"], "price"))
        normalized["budget"] = None if normalized["budget"] is None else str(_finite_decimal(normalized["budget"], "budget"))
        return normalized

    def _recommendation(self, recommendation_id: str) -> dict | None:
        row = self._db.execute("SELECT payload FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def _quantity_for(self, ceiling: Decimal, price: Decimal) -> int:
        quantity = int((ceiling / price).to_integral_value(rounding=ROUND_DOWN) // _LOT * _LOT)
        while quantity >= _LOT and price * quantity + _fee(price * quantity) > ceiling:
            quantity -= _LOT
        return quantity

    def _pending_reservations(self) -> Decimal:
        rows = self._db.execute("SELECT reserved_cash FROM orders WHERE status = 'pending'").fetchall()
        return sum((Decimal(row["reserved_cash"]) for row in rows), Decimal())

    def _available_cash(self) -> Decimal:
        fills = self._db.execute("SELECT side, price, quantity, fee FROM fills").fetchall()
        cash = _CAPITAL
        for fill in fills:
            gross = Decimal(fill["price"]) * fill["quantity"]
            cash += gross - Decimal(fill["fee"]) if fill["side"] == "sell" else -(gross + Decimal(fill["fee"]))
        return cash - self._pending_reservations()

    def _stock_exposure(self, code: str) -> Decimal:
        pending = self._db.execute("SELECT reserved_cash FROM orders WHERE code = ? AND status = 'pending'", (code,)).fetchall()
        position = next((p for p in self._positions() if p["code"] == code), None)
        return sum((Decimal(row["reserved_cash"]) for row in pending), Decimal()) + (position["cost_basis"] if position else Decimal())

    def _set_order_reason(self, order_id: str, reason: str) -> None:
        self._db.execute("UPDATE orders SET reason = ? WHERE id = ?", (reason, order_id))

    def _expire_order(self, order_id: str) -> None:
        self._db.execute("UPDATE orders SET status = 'expired', reason = 'expired', reserved_cash = '0' WHERE id = ?", (order_id,))

    def _expire_due(self, now: dt.datetime) -> None:
        for row in self._db.execute("""SELECT orders.id FROM orders
                                     JOIN recommendations ON recommendations.id = orders.recommendation_id
                                     WHERE orders.status = 'pending' AND recommendations.expires_at <= ?""", (now.isoformat(),)).fetchall():
            self._expire_order(row["id"])

    def _order(self, row: sqlite3.Row) -> dict:
        return dict(row)

    def _positions(self) -> list[dict]:
        rows = self._db.execute("SELECT code, name, side, filled_at, price, quantity, fee FROM fills ORDER BY filled_at, id").fetchall()
        positions: dict[str, dict] = {}
        for row in rows:
            item = positions.setdefault(row["code"], {"code": row["code"], "name": row["name"], "quantity": 0, "cost_basis": Decimal(), "latest_buy_at": None})
            gross = Decimal(row["price"]) * row["quantity"]
            if row["side"] == "buy":
                item["quantity"] += row["quantity"]
                item["cost_basis"] += gross + Decimal(row["fee"])
                item["latest_buy_at"] = row["filled_at"]
            elif item["quantity"]:
                sold = min(item["quantity"], row["quantity"])
                item["cost_basis"] -= item["cost_basis"] * Decimal(sold) / item["quantity"]
                item["quantity"] -= sold
        return [item for item in positions.values() if item["quantity"]]

    @staticmethod
    def _public_position(position: dict) -> dict:
        return {
            "code": position["code"], "name": position["name"], "quantity": position["quantity"],
            "cost_basis": str(position["cost_basis"]), "latest_buy_at": position["latest_buy_at"],
        }


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _finite_decimal(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if not amount.is_finite():
        raise ValueError(f"{field} must be finite")
    return amount
