"""Flat by the close, decided by Jev (the owner's rule, 2026-10-27).

For every stock held near the close, Jev reads the position, its stop, the day's
plan and news and answers close or hold:

- close (at least DECISION_CONFIDENCE): sell everything at market.
- hold (at least DECISION_CONFIDENCE) and the position has a stop: keep it, and
  swap the bracket's stop, which expires at the close, for a GTC stop at the same
  price, so the shares are never held overnight without one.
- anything else (low confidence, Jev unreachable, no stop to carry over): close.
  Flat by the close is the default; only a clear hold keeps a position.

Exits only: this never buys, and it touches only stocks the account holds.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from trades import stock_jev, stocks
from trades.config import Settings

DECISION_CONFIDENCE = 0.60
CLOSE_QUESTION = {
    "close": {
        "type": "choice",
        "instructions": "A long-only, cash-only day-trading desk holds `position` minutes before the US close. "
                        "Its rule is to be flat by the close unless there is a specific reason to hold "
                        "overnight under `stop`. Given `plan` and `news`, should it close or hold?",
        "criteria": {
            "close": "Default. The day trade is done, the case was intraday, the position is at a loss or "
                     "near its stop, `news` shows an event before the next open (earnings, a filing, a "
                     "halt), or the evidence for holding is missing.",
            "hold": "The position is in profit and well above `stop`, the setup in `plan` is a multi-day "
                    "one (a daily breakout or trend) rather than an opening-range trade, and `news` shows "
                    "nothing scheduled before the next open.",
        },
    },
}


def _stock_positions(broker) -> list[dict]:
    return [p for p in broker.positions() if p.get("asset_class") == "us_equity" and float(p.get("qty") or 0) > 0]


def _open_stop(broker, symbol: str) -> float | None:
    for o in broker.open_orders_with_legs(symbol):
        if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit") and o.get("stop_price"):
            return float(o["stop_price"])
    return None


def ask(position: dict, stop: float | None, trader_dir: Path, s: Settings, post=requests.post,
        news=stock_jev.news_for) -> dict:
    if not s.jev_configured:
        raise RuntimeError("Jev not configured")
    symbol = position["symbol"]
    shown = {k: position.get(k) for k in ("symbol", "qty", "avg_entry_price", "current_price",
                                           "unrealized_pl", "unrealized_plpc", "change_today")}
    state = {
        "position": stock_jev._text(shown),
        "stop": stock_jev._text(stop),
        "plan": stock_jev._text(stock_jev._read(trader_dir / "plan.json")),
        "news": stock_jev._text(news(symbol, trader_dir)),
    }
    body = {"model": s.jev_model, "state": state, "questions": CLOSE_QUESTION}
    headers = {"Authorization": f"Bearer {s.jev_key}"} if s.jev_key else {}
    response = post(s.jev_url, json=body, headers=headers, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Jev HTTP {response.status_code}")
    payload = response.json()
    choice = payload["answers"]["close"]
    if choice.get("type") != "choice" or choice.get("choice") not in ("close", "hold"):
        raise ValueError("Jev returned no close/hold decision")
    return {"decision": choice["choice"], "confidence": float(choice["confidence"]), "model": payload.get("model")}


def decide(answer: dict | None, stop: float | None) -> tuple[str, str]:
    """('close' | 'hold', why)."""
    if answer is None:
        return "close", "Jev unavailable: flat by the close"
    pct = f"{answer['confidence']:.0%}"
    if answer["confidence"] < DECISION_CONFIDENCE:
        return "close", f"Jev {answer['decision']} at only {pct}: flat by the close"
    if answer["decision"] == "close":
        return "close", f"Jev close ({pct})"
    if stop is None:
        return "close", f"Jev hold ({pct}) but no stop to carry overnight: flat by the close"
    return "hold", f"Jev hold ({pct}) under a GTC stop at {stop:g}"


def _stamp(kind: str, symbol: str) -> str:
    return f"{kind}-{symbol}-{time.time_ns()}"


def run(broker, s: Settings, trader_dir: Path, post=requests.post, execute: bool = True,
        news=stock_jev.news_for) -> list[dict]:
    out = []
    for p in _stock_positions(broker):
        symbol, qty = p["symbol"], float(p["qty"])
        stop = _open_stop(broker, symbol)
        try:
            answer = ask(p, stop, trader_dir, s, post, news)
        except Exception as exc:
            answer, error = None, str(exc)
        else:
            error = None
        action, why = decide(answer, stop)
        row = {"symbol": symbol, "qty": qty, "stop": stop, "action": action, "why": why,
               "jev": answer, "error": error, "done": None}
        if execute:
            try:
                if action == "close":
                    sell_qty = stocks.clear_for_sell(broker, symbol, qty)
                    r = broker.submit_order({"symbol": symbol, "qty": sell_qty, "side": "sell", "type": "market",
                                             "time_in_force": "day", "client_order_id": _stamp("flat", symbol)})
                    row["done"] = f"market sell {sell_qty} sent (order {r.get('id')})"
                else:
                    held = stocks.clear_for_sell(broker, symbol, qty)
                    r = broker.submit_order({"symbol": symbol, "qty": held, "side": "sell", "type": "stop",
                                             "stop_price": f"{stop:.2f}", "time_in_force": "gtc",
                                             "client_order_id": _stamp("hold", symbol)})
                    row["done"] = f"GTC stop at {stop:.2f} for {held} placed (order {r.get('id')})"
            except Exception as exc:
                row["done"] = f"FAILED: {exc}"
        out.append(row)
    log = trader_dir / "state" / "flatten.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    history = stock_jev._read(log) or []
    history.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": out})
    log.write_text(json.dumps(history[-60:], indent=1))
    return out
