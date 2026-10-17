"""Checks between an approved proposal and a paper order.

Freshness, the live account (the numeric guards again, on live facts), sizing
and buying power. An order goes out only when every check passes and
PAPER_EXECUTION is on; otherwise the order that would have been sent is
recorded and nothing is submitted.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone

from trades import guards
from trades.alpaca import AlpacaError
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


def order_for(p: Proposal, facts: dict, price: float) -> tuple[dict | None, str]:
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
            cost = int(order["qty"]) * quote["price"]
            if not _check(checks, "buying_power", cost <= facts["buying_power"],
                          f"${cost:,.0f} needed, ${facts['buying_power']:,.0f} available"):
                result["note"] = "not enough buying power"
                return result

        if not s.paper_execution:
            result["note"] = "all checks passed; PAPER_EXECUTION is off, so no order was sent"
            return result
        result["response"] = broker.submit_order(order)
        result["submitted"] = True
        result["note"] = "paper order submitted"
    except AlpacaError as exc:
        _check(checks, "broker", False, str(exc))
        result["note"] = "broker error"
    return result
