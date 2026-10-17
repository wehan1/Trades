"""Numeric guards: rules code can decide without judgment. Any failure blocks
the proposal before Jev is asked anything."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from trades.assets import is_crypto
from trades.config import Settings
from trades.proposal import Proposal

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$|^[A-Z0-9]{2,10}/(USD|USDT|USDC)$")


@dataclass
class GuardResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _position(p: Proposal) -> dict:
    position = p.account.get("position")
    return position if isinstance(position, dict) else {}


def schema(p: Proposal) -> GuardResult:
    problems = []
    if not isinstance(p.ticker, str) or not _TICKER.match(p.ticker):
        problems.append("ticker is not a valid symbol")
    if p.side not in ("buy", "sell"):
        problems.append("side must be buy or sell")
    size = _number(p.size_pct)
    if size is None or not 0 < size <= 100:
        problems.append("size_pct must be a number above 0 and at most 100")
    if not isinstance(p.reasoning, str) or not p.reasoning.strip():
        problems.append("reasoning is empty")
    for name in ("stop", "price"):
        value = getattr(p, name)
        if value is not None and ((_number(value) or 0) <= 0):
            problems.append(f"{name} must be a positive price when given")
    return GuardResult("schema", not problems, "; ".join(problems) or "all fields valid")


def opens_risk(p: Proposal) -> bool:
    """A buy, or a sell that would open or add to a short."""
    if p.side == "buy":
        return True
    return float(_position(p).get("qty") or 0) <= 0


def universe(p: Proposal, s: Settings) -> GuardResult:
    if not is_crypto(p.ticker):
        return GuardResult("universe", True, "stock")
    allowed = p.ticker in s.crypto_universe
    return GuardResult("universe", allowed, f"{p.ticker} {'is' if allowed else 'is not'} in CRYPTO_UNIVERSE "
                                            f"({', '.join(s.crypto_universe)})")


def no_short(p: Proposal, s: Settings) -> GuardResult:
    # Alpaca does not short crypto, whatever ALLOW_SHORT says.
    if p.side != "sell" or (s.allow_short and not is_crypto(p.ticker)):
        return GuardResult("no_short", True, "not a short sale" if p.side != "sell" else "shorting allowed")
    held = float(_position(p).get("qty") or 0)
    if held <= 0:
        return GuardResult("no_short", False, "sell with no long position would open a short; ALLOW_SHORT is off")
    return GuardResult("no_short", True, f"sells from a long position of {held:g}")


def position_size(p: Proposal, s: Settings) -> GuardResult:
    size = float(p.size_pct)
    if not opens_risk(p):
        return GuardResult("position_size", True, "reduces an existing position")
    equity = _number(p.account.get("equity"))
    held_value = abs(_number(_position(p).get("market_value")) or 0)
    held_pct = held_value / equity * 100 if equity else 0.0
    total = held_pct + size
    limit = s.max_crypto_position_pct if is_crypto(p.ticker) else s.max_position_pct
    detail = f"{size:g}% new + {held_pct:.1f}% held = {total:.1f}% (limit {limit:g}%)"
    return GuardResult("position_size", total <= limit + 1e-9, detail)


def crypto_exposure(p: Proposal, s: Settings) -> GuardResult:
    """Coins move together, so the sum of all crypto positions is capped too."""
    if not is_crypto(p.ticker) or not opens_risk(p):
        return GuardResult("crypto_exposure", True, "not new crypto risk")
    held = _number(p.account.get("crypto_exposure_pct"))
    if held is None:
        return GuardResult("crypto_exposure", True, "crypto exposure not given; checked again against the live account before any order")
    total = held + float(p.size_pct)
    return GuardResult("crypto_exposure", total <= s.max_crypto_exposure_pct + 1e-9,
                       f"{held:.1f}% in crypto + {float(p.size_pct):g}% new = {total:.1f}% "
                       f"(limit {s.max_crypto_exposure_pct:g}%)")


def stop_required(p: Proposal, s: Settings) -> GuardResult:
    """Crypto trades around the clock, so the stop has to be a price code can
    place as an order; a prose invalidation nobody is awake to act on is none."""
    if not is_crypto(p.ticker) or not opens_risk(p):
        return GuardResult("stop_required", True, "stock: a prose invalidation is judged by Jev" if not is_crypto(p.ticker) else "reduces risk")
    stop = _number(p.stop)
    return GuardResult("stop_required", stop is not None, f"stop {stop:g}" if stop is not None else "crypto entries need a numeric stop price")


def risk_per_trade(p: Proposal, s: Settings) -> GuardResult:
    """What the account loses if the stop is hit: size x distance to the stop."""
    stop, price = _number(p.stop), _number(p.price)
    if not opens_risk(p) or stop is None:
        return GuardResult("risk_per_trade", True, "no stop to measure risk from" if opens_risk(p) else "reduces risk")
    if price is None:
        return GuardResult("risk_per_trade", True, "no price given; checked again at the live price before any order")
    risk = float(p.size_pct) * abs(price - stop) / price
    return GuardResult("risk_per_trade", risk <= s.max_risk_per_trade_pct + 1e-9,
                       f"{float(p.size_pct):g}% x {abs(price - stop) / price:.1%} to stop = {risk:.2f}% of account "
                       f"(limit {s.max_risk_per_trade_pct:g}%)")


def adding_to_loser(p: Proposal, s: Settings) -> GuardResult:
    position = _position(p)
    qty = float(position.get("qty") or 0)
    pl = _number(position.get("unrealized_pl_pct"))
    same_direction = (p.side == "buy" and qty > 0) or (p.side == "sell" and qty < 0)
    if same_direction and pl is not None and pl < 0:
        return GuardResult("adding_to_loser", False, f"adds to a position that is {pl:.1f}% under water")
    return GuardResult("adding_to_loser", True, "does not add to a losing position")


def daily_loss(p: Proposal, s: Settings) -> GuardResult:
    pnl = _number(p.account.get("day_pnl_pct"))
    if pnl is None:
        return GuardResult("daily_loss", True, "day P&L not given; checked again against the live account before any order")
    if opens_risk(p) and pnl <= -s.max_daily_loss_pct:
        return GuardResult("daily_loss", False, f"day P&L {pnl:.2f}% is at or past the {s.max_daily_loss_pct:g}% loss limit")
    return GuardResult("daily_loss", True, f"day P&L {pnl:.2f}% (limit -{s.max_daily_loss_pct:g}%)")


def stop_side(p: Proposal, s: Settings) -> GuardResult:
    stop, price = _number(p.stop), _number(p.price)
    if stop is None or price is None or not opens_risk(p):
        return GuardResult("stop_side", True, "no price stop to check" if stop is None else "no price to compare")
    wrong = (p.side == "buy" and stop >= price) or (p.side == "sell" and stop <= price)
    side = "below" if p.side == "buy" else "above"
    return GuardResult("stop_side", not wrong, f"stop {stop:g} must be {side} price {price:g}")


def event_restriction(p: Proposal, s: Settings) -> GuardResult:
    if not opens_risk(p):
        return GuardResult("event_restriction", True, "reduces risk; event rules apply to new positions")
    date = p.trade_date()
    for rule in s.restrictions():
        tickers = rule.get("tickers") or []
        if rule.get("date") == date and ("*" in tickers or p.ticker in tickers):
            return GuardResult("event_restriction", False, f"{date}: {rule.get('reason', 'restricted')}")
    return GuardResult("event_restriction", True, f"no restriction on {date}")


RULES = (universe, no_short, position_size, crypto_exposure, adding_to_loser, daily_loss,
         stop_required, stop_side, risk_per_trade, event_restriction)


def run(p: Proposal, s: Settings) -> list[GuardResult]:
    """Schema first; the other rules only make sense on a well-formed proposal."""
    first = schema(p)
    if not first.passed:
        return [first]
    return [first] + [rule(p, s) for rule in RULES]
