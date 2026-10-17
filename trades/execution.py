"""Checks between an approved proposal and a paper order.

Freshness, the live account (the numeric guards again, on live facts), sizing
and buying power. Crypto trades around the clock and has no bracket orders, so
its stop goes in as a separate stop-limit once the entry fills. An order goes out only when every check passes and
PAPER_EXECUTION is on; otherwise the order that would have been sent is
recorded and nothing is submitted.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone

from trades import guards, protect
from trades.alpaca import AlpacaError
from trades.assets import is_crypto
from trades.config import Settings
from trades.proposal import Proposal


def _check(checks: list, name: str, passed: bool, detail: str) -> bool:
    checks.append({"name": name, "passed": passed, "detail": detail})
    return passed


def _age_minutes(as_of: str) -> float | None:
    try:
        stamp = datetime.fromisoformat(as_of)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 60


MIN_CRYPTO_NOTIONAL = 10.0


def order_for(p: Proposal, facts: dict, price: float) -> tuple[dict | None, str]:
    if is_crypto(p.ticker):
        return _crypto_order(p, facts, price)
    equity = float(facts["equity"])
    held = float((facts.get("position") or {}).get("qty") or 0)
    qty = math.floor(equity * float(p.size_pct) / 100 / price)
    if p.side == "sell" and held > 0:
        qty = min(qty, held) if qty >= 1 else 0
    if qty < 1:
        return None, f"{p.size_pct}% of ${equity:,.0f} buys less than one share at {price:g}"
    order = {
        "symbol": p.ticker, "qty": str(int(qty)), "side": p.side,
        "type": "market", "time_in_force": "day",
        # Alpaca refuses a reused client_order_id, so one proposal is one order at most.
        "client_order_id": f"trades-{p.id}",
    }
    if p.stop is not None and guards.opens_risk(p):
        order["order_class"] = "oto"
        order["stop_loss"] = {"stop_price": str(round(float(p.stop), 2))}
    return order, f"{int(qty)} shares at about {price:g} = ${qty * price:,.0f}"


def _crypto_order(p: Proposal, facts: dict, price: float) -> tuple[dict | None, str]:
    equity = float(facts["equity"])
    held = float((facts.get("position") or {}).get("qty") or 0)
    qty = math.floor(equity * float(p.size_pct) / 100 / price * 1e6) / 1e6
    if p.side == "sell":
        qty = min(qty, held)
    if qty * price < MIN_CRYPTO_NOTIONAL:
        return None, f"${qty * price:,.2f} is under the ${MIN_CRYPTO_NOTIONAL:g} minimum"
    order = {
        "symbol": p.ticker, "qty": protect.fmt_qty(qty), "side": p.side,
        "type": "market", "time_in_force": "gtc",
        # The stop rides in the order's ID, so a fresh session can recover it
        # from Alpaca even when no local log survived.
        "client_order_id": f"trades-{p.id}" + (f"-s{protect.fmt_price(float(p.stop))}" if p.stop is not None else ""),
    }
    return order, f"{protect.fmt_qty(qty)} {p.ticker.split('/')[0]} at about {price:g} = ${qty * price:,.0f}"


def _fit_risk(p: Proposal, s: Settings, checks: list) -> Proposal:
    """Shrink a position whose stop-out would cost more than the risk budget.

    Agent proposals arrive without a price, so their risk is first known here.
    Cutting size only ever lowers risk; a stop on the wrong side of the price is
    left for the stop_side guard to block."""
    if p.stop is None or not guards.opens_risk(p):
        return p
    distance = (p.price - float(p.stop)) / p.price if p.side == "buy" else (float(p.stop) - p.price) / p.price
    if distance <= 0:
        return p
    cap = math.floor(s.max_risk_per_trade_pct / distance * 100) / 100
    if float(p.size_pct) <= cap:
        return p
    _check(checks, "risk_sizing", True, f"size cut from {float(p.size_pct):g}% to {cap:g}%: the stop is "
                                        f"{distance:.1%} away and a stop-out may cost {s.max_risk_per_trade_pct:g}% of account")
    return replace(p, size_pct=cap)


def _records(s: Settings) -> list[dict]:
    from trades import store
    return store.load(s.data_dir)


def run(record: dict, s: Settings, broker) -> dict:
    """Run the execution checks for a checked record and maybe submit. Returns
    the execution section of the record."""
    checks: list[dict] = []
    result = {"checks": checks, "order": None, "submitted": False, "response": None, "note": ""}
    p = Proposal.from_dict(record["proposal"])

    if not _check(checks, "approved", record["decision"] == "APPROVE", record.get("blocked_by") or "approved"):
        result["note"] = "blocked before execution"
        return result

    age = _age_minutes(p.as_of)
    if not _check(checks, "freshness", age is not None and age <= s.proposal_max_age_min,
                  f"{age:.0f} min old (limit {s.proposal_max_age_min:g})" if age is not None else "no timestamp"):
        result["note"] = "proposal too old to act on"
        return result

    if broker is None:
        _check(checks, "broker", False, "Alpaca keys not set")
        result["note"] = "no broker"
        return result

    try:
        crypto = is_crypto(p.ticker)
        if crypto:
            _check(checks, "market_open", True, "crypto trades 24/7")
        else:
            clock = broker.clock()
            if not _check(checks, "market_open", bool(clock.get("is_open")),
                          "open" if clock.get("is_open") else f"closed; next open {clock.get('next_open')}"):
                result["note"] = "market closed"
                return result

        facts = broker.account_facts(p.ticker)
        if not _check(checks, "account", facts.get("status") == "ACTIVE" and not facts.get("trading_blocked"),
                      f"status {facts.get('status')}, trading blocked: {facts.get('trading_blocked')}"):
            result["note"] = "account cannot trade"
            return result

        quote = broker.latest_trade(p.ticker)
        # Live facts replace whatever the proposal was written against.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        live = replace(p, account=facts, calendar={**p.calendar, "date": today}, price=quote["price"])
        live = _fit_risk(live, s, checks)
        failed = [g for g in guards.run(live, s) if not g.passed]
        if not _check(checks, "live_guards", not failed,
                      "; ".join(f"{g.name}: {g.detail}" for g in failed) or "all numeric guards pass on live account"):
            result["note"] = "fails a numeric guard on the live account"
            return result

        order, sizing = order_for(live, facts, quote["price"])
        if not _check(checks, "size", order is not None, sizing):
            result["note"] = "position too small"
            return result
        result["order"] = order

        if p.side == "buy":
            cost = float(order["qty"]) * quote["price"]
            if not _check(checks, "buying_power", cost <= facts["buying_power"],
                          f"${cost:,.0f} needed, ${facts['buying_power']:,.0f} available"):
                result["note"] = "not enough buying power"
                return result

        if not s.paper_execution:
            result["note"] = "all checks passed; PAPER_EXECUTION is off, so no order was sent"
            return result
        if crypto and p.side == "sell":
            # Open sell orders (the stop) reserve the coins this sale needs.
            for o in broker.open_orders(p.ticker):
                broker.cancel_order(o["id"])
        result["response"] = broker.submit_order(order)
        result["submitted"] = True
        result["note"] = "paper order submitted"
        if crypto:
            fill = broker.wait_for_fill(result["response"]["id"])
            if fill.get("status") != "filled":
                result["note"] += f"; entry {fill.get('status', 'unknown')}, stop not placed yet: run `trades protect`"
                return result
            stop = float(p.stop) if p.side == "buy" else protect.recorded_stops(_records(s)).get(p.ticker)
            result["stop"] = protect.ensure(broker, s, p.ticker, stop)
            result["note"] += f"; stop: {result['stop']['note']}"
    except AlpacaError as exc:
        _check(checks, "broker", False, str(exc))
        result["note"] = "broker error"
    return result
