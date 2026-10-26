"""Send a day-trader-skill stock order to the Alpaca paper account.

The vendored broker skill (.claude/skills/trader-broker) expects Alpaca's MCP
server in Claude Desktop. This cloud setup has none, so this module is that
last step: it sends only an order the skill's own records show as approved
today, which takes a trader-risk PASS for that exact order (matched by
fingerprint) and the owner's typed yes recorded by `ticket.py approve`. A
buy must also PASS the sharia screen (trades/sharia.py). It
never decides anything itself, and the broker URL is the fixed paper one.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

from trades.config import ROOT

TICKET = ROOT / ".claude" / "skills" / "trader-broker" / "ticket.py"


class SendError(Exception):
    pass


def _ticket():
    spec = importlib.util.spec_from_file_location("trader_ticket", TICKET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rest_order(tool_input: dict, client_order_id: str) -> dict:
    """place_stock_order's flat arguments as Alpaca's REST order body."""
    body = {k: v for k, v in tool_input.items()
            if k not in ("take_profit_limit_price", "stop_loss_stop_price", "stop_loss_limit_price",
                         "advanced_instructions")}
    if "take_profit_limit_price" in tool_input:
        body["take_profit"] = {"limit_price": tool_input["take_profit_limit_price"]}
    stop = {}
    if "stop_loss_stop_price" in tool_input:
        stop["stop_price"] = tool_input["stop_loss_stop_price"]
    if "stop_loss_limit_price" in tool_input:
        stop["limit_price"] = tool_input["stop_loss_limit_price"]
    if stop:
        body["stop_loss"] = stop
    # One approval is one order: Alpaca refuses a reused client_order_id.
    body.setdefault("client_order_id", client_order_id)
    return body


def approved_order(index: int, trader_dir: Path, now: str | None = None):
    """(order, fingerprint, tool_input) for an order approved today, else SendError."""
    t = _ticket()
    doc = t.read_json(str(trader_dir / "orders.json"), "orders file")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not 1 <= index <= len(orders):
        raise SendError(f"there is no order {index} in {trader_dir / 'orders.json'}")
    cfg = t.read_json(str(trader_dir / "config.json"), "config", required=False) or {}
    if cfg.get("paper", True) is not True:
        raise SendError("config.json says paper is false; this bridge sends paper orders only")
    risk = t.read_json(str(trader_dir / "state" / "risk.json"), "risk check", required=False)
    state = t.load_state(str(trader_dir / "state" / "broker.json"))
    today = t.to_eastern(t.parse_now(now)).date().isoformat()
    order = orders[index - 1]
    fp = t.fingerprint(order)
    verdict, why = t.risk_verdict(risk, fp, today)
    if verdict is None or verdict.get("verdict") != "PASS":
        raise SendError(f"trader-risk has not passed order {index} today: {why or verdict}")
    rec = t.latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise SendError(f"order {index} has no yes recorded today (ticket.py approve), or was already sent")
    return order, fp, t.tool_input(order)


def sharia_gate(order: dict, broker, screen) -> dict | None:
    """Buys must pass the sharia screen; a sell only exits what is held and is never blocked."""
    if str(order.get("side", "")).lower() != "buy":
        return None
    symbol = str(order["symbol"]).upper()
    try:
        price = float(broker.latest_trade(symbol)["price"])
    except Exception:
        price = float(order.get("limit_price") or (order.get("plan") or {}).get("entry") or 0) or None
    verdict = screen(symbol, price)
    if verdict.verdict != "PASS":
        raise SendError(f"{symbol} is {verdict.verdict} on the sharia screen: "
                        + ("; ".join(verdict.reasons) or "no reason recorded"))
    return verdict.to_dict()


def send(index: int, broker, trader_dir: Path, now: str | None = None, screen=None) -> dict:
    from trades import stock_jev
    order, fp, tool_input = approved_order(index, trader_dir, now)
    t = _ticket()
    stock_jev.gate(order, fp, trader_dir, t.to_eastern(t.parse_now(now)).date().isoformat())
    try:
        sharia = sharia_gate(order, broker, screen or _screen_live)
    except Exception as exc:
        # A failed or unavailable screen blocks the buy; it is recorded like any refusal.
        _record(index, trader_dir, "failed", note=f"not sent: {exc}"[:300], now=now)
        raise SendError(str(exc)) from exc
    body = rest_order(tool_input, f"dts-{fp}")
    if str(order.get("side", "")).lower() == "sell":
        try:
            body["qty"] = clear_for_sell(broker, str(order["symbol"]).upper(), body.get("qty"))
        except Exception as exc:
            _record(index, trader_dir, "failed", note=f"not sent: {exc}"[:300], now=now)
            raise SendError(str(exc)) from exc
    try:
        response = broker.submit_order(body)
    except Exception as exc:
        _record(index, trader_dir, "failed", note=str(exc)[:300], now=now)
        raise SendError(f"Alpaca refused order {index}: {exc}") from exc
    _record(index, trader_dir, "placed", broker_id=response.get("id"), now=now)
    return {"order": body, "response": response, "sharia": sharia}


def clear_for_sell(broker, symbol: str, qty, wait: float = 15.0, poll: float = 0.5) -> str:
    """Make an approved sell possible: Alpaca refuses a sell while a bracket's stop and
    target legs hold the shares. Cancels only this symbol's open sell orders, waits
    until Alpaca shows none, and returns the quantity to sell (never more than held)."""
    position = broker.position(symbol)
    held = float(position["qty"]) if position else 0.0
    if held <= 0:
        raise SendError(f"nothing to sell: no {symbol} shares held (did the stop or target already fill?)")
    for o in broker.open_orders_with_legs(symbol):
        if o.get("side") == "sell":
            broker.cancel_order(o["id"])
    deadline = time.monotonic() + wait
    while any(o.get("side") == "sell" for o in broker.open_orders_with_legs(symbol)):
        if time.monotonic() > deadline:
            raise SendError(f"{symbol}'s stop and target orders did not cancel in {wait:g}s; nothing sold")
        time.sleep(poll)
    want = float(qty) if qty not in (None, "") else held
    return f"{min(want, held):g}"


def _screen_live(symbol: str, price: float | None):
    from trades import config, sharia
    return sharia.fetch_and_screen(symbol, price, config.load().data_dir / "sharia")


def _record(index: int, trader_dir: Path, status: str, broker_id: str | None = None,
            note: str | None = None, now: str | None = None):
    """Write the outcome into the skill's own log with its own command."""
    args = [sys.executable, str(TICKET), "result", "--index", str(index), "--status", status,
            "--orders", str(trader_dir / "orders.json"), "--config", str(trader_dir / "config.json"),
            "--risk", str(trader_dir / "state" / "risk.json"), "--state", str(trader_dir / "state" / "broker.json")]
    if broker_id:
        args += ["--broker-id", broker_id]
    if note:
        args += ["--note", note]
    if now:
        args += ["--now", now]
    subprocess.run(args, check=False, capture_output=True, text=True)
