"""Keep every crypto position under a stop.

Crypto has no bracket orders on Alpaca, so the stop is a separate GTC
stop-limit sell. This places a missing one, resizes one that no longer matches
the position, and sells at market when the price is already through the stop
(a stop-limit can be jumped). Run it hourly; it acts only with PAPER_EXECUTION
on and otherwise reports what it would do.
"""

from __future__ import annotations

import math
import time

from trades.assets import crypto_pair, is_crypto
from trades.config import Settings


def fmt_price(x: float) -> str:
    return f"{round(x, 2) if x >= 1 else round(x, 6)}"


def fmt_qty(x: float) -> str:
    return f"{math.floor(x * 1e6) / 1e6:.6f}".rstrip("0").rstrip(".")


def stop_order(pair: str, qty: float, stop: float, s: Settings) -> dict:
    limit = stop * (1 - s.crypto_stop_limit_buffer_pct / 100)
    return {
        "symbol": pair, "qty": fmt_qty(qty), "side": "sell", "type": "stop_limit",
        "stop_price": fmt_price(stop), "limit_price": fmt_price(limit), "time_in_force": "gtc",
        "client_order_id": f"stop-{pair.replace('/', '')}-{time.time_ns()}",
    }


def recorded_stops(records: list[dict]) -> dict[str, float]:
    """The stop of the latest submitted buy for each pair."""
    stops = {}
    for r in records:
        p = r.get("proposal") or {}
        if (p.get("side") == "buy" and p.get("stop") is not None and is_crypto(p.get("ticker", ""))
                and (r.get("execution") or {}).get("submitted")):
            stops[p["ticker"]] = float(p["stop"])
    return stops


def stop_from_orders(broker, pair: str) -> float | None:
    """The stop stamped into the newest filled entry's client_order_id ("-s<price>")."""
    for o in broker.filled_buys(pair):
        tag = (o.get("client_order_id") or "").rpartition("-s")[2]
        if (o.get("client_order_id") or "").startswith("trades-") and tag:
            try:
                return float(tag)
            except ValueError:
                continue
    return None


def ensure(broker, s: Settings, pair: str, stop: float | None, price: float | None = None) -> dict:
    """Bring one pair's stop in line with its position. Returns what was found and done."""
    position = broker.position(pair)
    qty = float(position.get("qty_available") or position["qty"]) if position else 0.0
    open_orders = broker.open_orders(pair)
    stops_open = [o for o in open_orders if o.get("type") == "stop_limit" and o.get("side") == "sell"]
    action = {"pair": pair, "qty": qty, "stop": stop, "done": [], "note": ""}
    if qty <= 0:
        action["note"] = "no position"
        return action
    if stop is None:
        action["note"] = "no recorded stop; protect this position by hand"
        return action
    if price is None:
        price = float(position.get("current_price") or broker.latest_trade(pair)["price"])
    action["price"] = price

    if price <= stop:
        action["note"] = f"price {price:g} is at or below stop {stop:g}: sell at market"
        if s.paper_execution:
            for o in open_orders:
                broker.cancel_order(o["id"])
            held = float(broker.position(pair)["qty"])
            order = {"symbol": pair, "qty": fmt_qty(held), "side": "sell", "type": "market",
                     "time_in_force": "gtc", "client_order_id": f"stopout-{pair.replace('/', '')}-{time.time_ns()}"}
            action["done"].append(broker.submit_order(order).get("id"))
        return action

    covered = sum(float(o.get("qty") or 0) for o in stops_open)
    same_stop = all(abs(float(o.get("stop_price") or 0) - stop) < 1e-6 for o in stops_open)
    if stops_open and same_stop and abs(covered - qty) <= max(1e-6, qty * 1e-4):
        action["note"] = "stop in place"
        return action

    action["note"] = (f"replace stop: {covered:g} covered at the wrong level or size" if stops_open
                      else "no stop order: place one")
    if s.paper_execution:
        for o in stops_open:
            broker.cancel_order(o["id"])
        # Sell orders reserve the coins, so read what is free after cancelling.
        held = broker.position(pair)
        free = float(held.get("qty_available") or held["qty"])
        action["done"].append(broker.submit_order(stop_order(pair, free, stop, s)).get("id"))
    return action


def run(s: Settings, broker, records: list[dict]) -> list[dict]:
    stops = recorded_stops(records)
    actions = []
    for position in broker.positions():
        if position.get("asset_class") != "crypto":
            continue
        pair = crypto_pair(position["symbol"]) or position["symbol"]
        stop = stops.get(pair) or stop_from_orders(broker, pair)
        actions.append(ensure(broker, s, pair, stop, float(position["current_price"])))
    return actions
