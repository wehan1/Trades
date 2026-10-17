"""Crypto pair spellings. Inside this repo a pair is Alpaca's order form, BTC/USD."""

from __future__ import annotations

import re

_PAIR = re.compile(r"^([A-Z0-9]{2,10})[/\-](USD|USDT|USDC)$")
_BARE = re.compile(r"^([A-Z0-9]{2,10})(USD|USDT|USDC)$")
# A bare BTCUSD is read as crypto only for these bases, so a stock ticker that
# happens to end in USD is not mistaken for a pair.
KNOWN_BASES = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "DOT", "AVAX", "LINK",
    "UNI", "AAVE", "SHIB", "XTZ", "SUSHI", "YFI", "BAT", "CRV", "GRT", "MKR", "PEPE", "TRUMP",
})


def crypto_pair(raw: str) -> str | None:
    """BTC/USD for BTC/USD, BTC-USD, btcusd and the like; None for anything else."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper()
    if m := _PAIR.match(s):
        return f"{m.group(1)}/{m.group(2)}"
    if (m := _BARE.match(s)) and m.group(1) in KNOWN_BASES:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def is_crypto(ticker: str) -> bool:
    return crypto_pair(ticker) is not None


def position_symbol(ticker: str) -> str:
    """Alpaca names crypto positions without the slash: BTCUSD."""
    return ticker.replace("/", "")


def yahoo_symbol(ticker: str) -> str:
    """TradingAgents reads crypto through Yahoo, which spells it BTC-USD."""
    return ticker.replace("/", "-")
