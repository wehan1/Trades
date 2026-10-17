"""Alpaca paper trading over its REST API. The base URL is fixed to paper."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

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
        return self._get(ALPACA_TRADING_URL, f"/v2/positions/{symbol}")

    def latest_trade(self, symbol: str) -> dict:
        """{"price": float, "time": iso} from the free IEX feed."""
        data = self._get(ALPACA_DATA_URL, f"/v2/stocks/{symbol}/trades/latest", feed="iex")
        if not data or "trade" not in data:
            raise AlpacaError(f"no latest trade for {symbol}")
        return {"price": float(data["trade"]["p"]), "time": data["trade"]["t"]}

    def todays_fills(self) -> list[dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._get(ALPACA_TRADING_URL, "/v2/account/activities/FILL", date=today) or []

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        return self._get(ALPACA_TRADING_URL, "/v2/account/portfolio/history",
                         period=period, timeframe=timeframe)

    def submit_order(self, order: dict) -> dict:
        response = self.http.post(ALPACA_TRADING_URL + "/v2/orders", json=order, timeout=20)
        if response.status_code not in (200, 201):
            raise AlpacaError(f"order rejected: HTTP {response.status_code} {response.text[:300]}")
        return response.json()

    def account_facts(self, symbol: str) -> dict:
        """The account context a proposal is judged in, from the live paper account."""
        account = self.account()
        equity, last_equity = float(account["equity"]), float(account["last_equity"])
        facts = {
            "equity": equity,
            "buying_power": float(account["buying_power"]),
            "day_pnl_pct": round((equity - last_equity) / last_equity * 100, 3) if last_equity else 0.0,
            "status": account.get("status"),
            "trading_blocked": bool(account.get("trading_blocked")),
        }
        position = self.position(symbol)
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
