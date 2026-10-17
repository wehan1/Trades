"""Alpaca paper trading over its REST API. The base URL is fixed to paper.

Crypto differs from stocks here: prices come from the crypto data endpoint,
positions are named without the slash (BTCUSD), orders are GTC, and there is
no bracket/OTO order, so a stop is a separate stop-limit order placed after
the entry fills.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from trades.assets import is_crypto, position_symbol
from trades.config import ALPACA_DATA_URL, ALPACA_TRADING_URL, Settings


class AlpacaError(Exception):
    pass


class AlpacaPaper:
    def __init__(self, s: Settings, session: requests.Session | None = None):
        if not s.alpaca_configured:
            raise AlpacaError("no Alpaca keys (set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY)")
        self.http = session or requests.Session()
        self.http.headers.update({
            "APCA-API-KEY-ID": s.alpaca_key,
            "APCA-API-SECRET-KEY": s.alpaca_secret,
        })

    def _get(self, base: str, path: str, **params):
        response = self.http.get(base + path, params=params or None, timeout=20)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise AlpacaError(f"GET {path}: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    def account(self) -> dict:
        return self._get(ALPACA_TRADING_URL, "/v2/account")

    def clock(self) -> dict:
        return self._get(ALPACA_TRADING_URL, "/v2/clock")

    def position(self, symbol: str) -> dict | None:
        return self._get(ALPACA_TRADING_URL, f"/v2/positions/{position_symbol(symbol)}")

    def positions(self) -> list[dict]:
        return self._get(ALPACA_TRADING_URL, "/v2/positions") or []

    def latest_trade(self, symbol: str) -> dict:
        """{"price": float, "time": iso}: IEX feed for stocks, Alpaca's US crypto feed for pairs."""
        if is_crypto(symbol):
            data = self._get(ALPACA_DATA_URL, "/v1beta3/crypto/us/latest/trades", symbols=symbol)
            trade = ((data or {}).get("trades") or {}).get(symbol)
        else:
            data = self._get(ALPACA_DATA_URL, f"/v2/stocks/{symbol}/trades/latest", feed="iex")
            trade = (data or {}).get("trade")
        if not trade:
            raise AlpacaError(f"no latest trade for {symbol}")
        return {"price": float(trade["p"]), "time": trade["t"]}

    def daily_closes(self, pair: str, start: str) -> list[tuple[str, float]]:
        """Daily crypto closes from ``start`` (YYYY-MM-DD), oldest first."""
        closes, token = [], None
        while True:
            params = {"symbols": pair, "timeframe": "1Day", "start": start, "limit": 1000}
            if token:
                params["page_token"] = token
            data = self._get(ALPACA_DATA_URL, "/v1beta3/crypto/us/bars", **params) or {}
            closes += [(b["t"], float(b["c"])) for b in (data.get("bars") or {}).get(pair, [])]
            token = data.get("next_page_token")
            if not token:
                return closes

    def todays_fills(self) -> list[dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._get(ALPACA_TRADING_URL, "/v2/account/activities/FILL", date=today) or []

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        return self._get(ALPACA_TRADING_URL, "/v2/account/portfolio/history",
                         period=period, timeframe=timeframe)

    def open_orders(self, symbol: str) -> list[dict]:
        return self._get(ALPACA_TRADING_URL, "/v2/orders", status="open", symbols=symbol) or []

    def order(self, order_id: str) -> dict:
        return self._get(ALPACA_TRADING_URL, f"/v2/orders/{order_id}")

    def cancel_order(self, order_id: str) -> None:
        response = self.http.delete(f"{ALPACA_TRADING_URL}/v2/orders/{order_id}", timeout=20)
        if response.status_code not in (200, 204, 404):
            raise AlpacaError(f"cancel {order_id}: HTTP {response.status_code} {response.text[:200]}")

    def submit_order(self, order: dict) -> dict:
        response = self.http.post(ALPACA_TRADING_URL + "/v2/orders", json=order, timeout=20)
        if response.status_code not in (200, 201):
            raise AlpacaError(f"order rejected: HTTP {response.status_code} {response.text[:300]}")
        return response.json()

    def wait_for_fill(self, order_id: str, timeout: float = 30.0) -> dict:
        """Poll until the order is filled or the timeout passes; returns its last state."""
        deadline = time.monotonic() + timeout
        while True:
            state = self.order(order_id) or {}
            if state.get("status") in ("filled", "canceled", "expired", "rejected") or time.monotonic() > deadline:
                return state
            time.sleep(1.0)

    def account_facts(self, symbol: str) -> dict:
        """The account context a proposal is judged in, from the live paper account."""
        account = self.account()
        equity, last_equity = float(account["equity"]), float(account["last_equity"])
        facts = {
            "equity": equity,
            "buying_power": float(account.get("non_marginable_buying_power") or account["buying_power"])
                            if is_crypto(symbol) else float(account["buying_power"]),
            "day_pnl_pct": round((equity - last_equity) / last_equity * 100, 3) if last_equity else 0.0,
            "status": account.get("crypto_status") if is_crypto(symbol) else account.get("status"),
            "trading_blocked": bool(account.get("trading_blocked")),
        }
        held = self.positions()
        crypto_value = sum(abs(float(p["market_value"])) for p in held if p.get("asset_class") == "crypto")
        facts["crypto_exposure_pct"] = round(crypto_value / equity * 100, 2) if equity else 0.0
        position = next((p for p in held if p.get("symbol") == position_symbol(symbol)), None)
        if position:
            facts["position"] = {
                "qty": float(position["qty"]),
                "avg_entry_price": float(position["avg_entry_price"]),
                "market_value": float(position["market_value"]),
                "unrealized_pl_pct": round(float(position["unrealized_plpc"]) * 100, 2),
            }
        facts["recent_trades"] = [
            f"{f.get('side')} {f.get('qty')} {f.get('symbol')} @ {f.get('price')}"
            for f in self.todays_fills()[:20]
        ]
        return facts
