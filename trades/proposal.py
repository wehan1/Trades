"""A written trade proposal and the account/calendar context it was made in."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from trades.assets import crypto_pair, is_crypto


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Proposal:
    ticker: str
    side: str                    # "buy" or "sell"
    size_pct: float              # percent of account equity
    reasoning: str
    stop: float | None = None    # absolute stop price
    invalidation: str = ""       # prose invalidation, when there is no price stop
    price: float | None = None   # last price the proposer saw
    as_of: str = field(default_factory=now_iso)
    source: str = "pasted"       # agent, pasted or test
    # equity, day_pnl_pct, position {qty, avg_entry_price, market_value,
    # unrealized_pl_pct}, recent_trades [str]
    account: dict = field(default_factory=dict)
    # date (YYYY-MM-DD), events [str]
    calendar: dict = field(default_factory=dict)
    label: dict | None = None    # expected outcome, synthetic test ideas only
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def from_dict(cls, raw: dict) -> "Proposal":
        """Build from untrusted input without judging it; the schema guard does that."""
        if not isinstance(raw, dict):
            raise TypeError("a proposal must be a JSON object")
        known = {f for f in cls.__dataclass_fields__}
        values = {k: v for k, v in raw.items() if k in known}
        values.setdefault("ticker", "")
        values.setdefault("side", "")
        values.setdefault("size_pct", None)
        values.setdefault("reasoning", "")
        if isinstance(values["ticker"], str):
            values["ticker"] = crypto_pair(values["ticker"]) or values["ticker"].strip().upper()
        if isinstance(values["side"], str):
            values["side"] = values["side"].strip().lower()
        for key in ("account", "calendar"):
            if not isinstance(values.get(key, {}), dict):
                values[key] = {}
        return cls(**values)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def asset_class(self) -> str:
        return "crypto" if is_crypto(self.ticker) else "stock"

    def trade_date(self) -> str:
        return str(self.calendar.get("date") or self.as_of[:10])
