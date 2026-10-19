"""Intraday, long-only: two setups on 15-minute candles in the London and New
York sessions, backtested on Alpaca history before any paper trading.

Only buys open risk; a position is closed by selling the coins it holds, never
by shorting, and nothing is bought on margin (cash only).

Setups (entries; each adds a tranche to the coin's position):
  orb   opening-range breakout: after a session's first 30 minutes, a 15-minute
        close above that range's high and above the session VWAP. Stop: the
        range's low.
  vwap  VWAP reclaim: in a session that has already traded above its VWAP, a
        close back above VWAP after a close below it. Stop: the lowest low of
        the last four bars.
Exit (whole position): a 15-minute close below both the 20-bar EMA and the
session VWAP, or a tranche's stop trading through.

With ``rsi_filter`` on, entries also need RSI between 50 and 70: rising, not
overbought.

Entries are taken only on days with a long bias, here a rule (the prior daily
close above its 50-day average) standing in for the AI desk's daily rating:
the desk cannot be backtested honestly, because its models know how past days
ended.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SESSIONS = {
    "london": (ZoneInfo("Europe/London"), time(8, 0), time(16, 30)),
    "new_york": (ZoneInfo("America/New_York"), time(9, 30), time(16, 0)),
}
SETUPS = ("orb", "vwap")


@dataclass(frozen=True)
class Params:
    setup: str = "orb"
    max_exposure_pct: float = 75.0      # all coins together, cash only
    risk_pct: float = 3.0               # account loss if one entry's stop is hit
    daily_loss_pct: float = 6.0         # no new entries once the day is down this much
    max_entries_per_coin_day: int = 2
    fee_pct: float = 0.25               # per side; Alpaca crypto taker fee, tier 1
    slippage_pct: float = 0.05          # per side, against us
    opening_range_bars: int = 2
    ema_bars: int = 20
    stop_lookback_bars: int = 4         # vwap setup: stop at the lowest low of these bars
    rsi_filter: bool = False            # enter only while rsi_low < RSI < rsi_high
    rsi_bars: int = 14
    rsi_low: float = 50.0
    rsi_high: float = 70.0
    bias_days: int = 50
    min_stop_pct: float = 0.15          # stops closer than this are noise, skip


@dataclass
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    vw: float


def scaled(p: Params, minutes: int) -> Params:
    """The same rules on ``minutes``-minute candles: bar-count windows scaled to cover
    the clock time they cover on 15-minute candles (30-minute range, 20-bar EMA, 4-bar stop,
    14-bar RSI)."""
    if 15 % minutes and minutes % 15:
        raise ValueError(f"{minutes}-minute candles do not divide evenly into 15-minute windows")
    f = 15 / minutes
    return replace(p, opening_range_bars=round(p.opening_range_bars * f), ema_bars=round(p.ema_bars * f),
                   stop_lookback_bars=round(p.stop_lookback_bars * f), rsi_bars=round(p.rsi_bars * f))


def session_of(t: datetime) -> tuple[str, date] | None:
    """The session a bar's open time falls in, keyed by its local trading date."""
    for name, (tz, start, end) in SESSIONS.items():
        local = t.astimezone(tz)
        if start <= local.time() < end:
            return name, local.date()
    return None


# --- data -------------------------------------------------------------------

def fetch_bars(get, pair: str, timeframe: str, start: str, end: str, cache_dir: Path) -> list[Bar]:
    """Alpaca crypto bars, cached on disk. ``get(path, **params)`` returns JSON."""
    cache = cache_dir / f"{pair.replace('/', '')}_{timeframe}_{start}_{end}.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        raw, token = [], None
        while True:
            params = {"symbols": pair, "timeframe": timeframe, "start": start, "end": end, "limit": 10000}
            if token:
                params["page_token"] = token
            data = get("/v1beta3/crypto/us/bars", **params) or {}
            raw += (data.get("bars") or {}).get(pair, [])
            token = data.get("next_page_token")
            if not token:
                break
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw))
    return [Bar(datetime.fromisoformat(b["t"].replace("Z", "+00:00")), float(b["o"]), float(b["h"]),
                float(b["l"]), float(b["c"]), float(b["v"]), float(b.get("vw") or b["c"])) for b in raw]


def daily_bias(daily: list[Bar], days: int) -> dict[date, bool]:
    """For each day, whether the prior close was above the average of the prior ``days`` closes."""
    bias, closes = {}, []
    for bar in daily:
        if len(closes) >= days:
            bias[bar.t.date()] = closes[-1] > sum(closes[-days:]) / days
        closes.append(bar.c)
    return bias


# --- signals ----------------------------------------------------------------

@dataclass
class _Tape:
    """Running per-coin indicators, updated one closed bar at a time."""
    ema: float | None = None
    session: tuple | None = None
    pv: float = 0.0
    vol: float = 0.0
    bars_in_session: int = 0
    or_high: float = -math.inf
    or_low: float = math.inf
    traded_above_vwap: bool = False
    prev_close_below_vwap: bool = False
    entered_this_session: bool = False
    recent_lows: list = field(default_factory=list)
    prev_close: float | None = None
    avg_gain: float = 0.0
    avg_loss: float = 0.0
    changes: int = 0
    rsi: float | None = None

    def update(self, bar: Bar, sess: tuple | None, p: Params) -> float | None:
        k = 2 / (p.ema_bars + 1)
        self.ema = bar.c if self.ema is None else self.ema + k * (bar.c - self.ema)
        self.recent_lows = (self.recent_lows + [bar.l])[-p.stop_lookback_bars:]
        self._update_rsi(bar.c, p.rsi_bars)
        if sess != self.session:
            self.session, self.pv, self.vol, self.bars_in_session = sess, 0.0, 0.0, 0
            self.or_high, self.or_low, self.traded_above_vwap = -math.inf, math.inf, False
            self.prev_close_below_vwap = False
            self.entered_this_session = False
        if sess is None:
            return None
        self.bars_in_session += 1
        self.pv += bar.vw * bar.v
        self.vol += bar.v
        vwap = self.pv / self.vol if self.vol > 0 else bar.c
        if self.bars_in_session <= p.opening_range_bars:
            self.or_high, self.or_low = max(self.or_high, bar.h), min(self.or_low, bar.l)
        return vwap


    def _update_rsi(self, close: float, n: int):
        """Wilder's RSI: a simple average of the first ``n`` changes, smoothed after."""
        if self.prev_close is not None:
            gain, loss = max(close - self.prev_close, 0.0), max(self.prev_close - close, 0.0)
            self.changes += 1
            w = 1 / min(self.changes, n)
            self.avg_gain += w * (gain - self.avg_gain)
            self.avg_loss += w * (loss - self.avg_loss)
            if self.changes >= n:
                self.rsi = 100.0 if self.avg_loss == 0 else 100 - 100 / (1 + self.avg_gain / self.avg_loss)
        self.prev_close = close


def entry_signal(tape: _Tape, bar: Bar, vwap: float, p: Params) -> float | None:
    """The stop price if this closed bar triggers an entry, else None."""
    if p.rsi_filter and not (tape.rsi is not None and p.rsi_low < tape.rsi < p.rsi_high):
        return None
    if p.setup == "orb":
        # One breakout per session: the range breaks once.
        if (not tape.entered_this_session and tape.bars_in_session > p.opening_range_bars
                and bar.c > tape.or_high and bar.c > vwap):
            return tape.or_low
        return None
    signal = tape.traded_above_vwap and tape.prev_close_below_vwap and bar.c > vwap
    return min(tape.recent_lows) if signal else None


def exit_signal(tape: _Tape, bar: Bar, vwap: float | None) -> bool:
    return vwap is not None and bar.c < tape.ema and bar.c < vwap


# --- simulation ---------------------------------------------------------------

@dataclass
class Tranche:
    qty: float
    entry: float
    stop: float
    session: str = ""


def backtest(bars: dict[str, list[Bar]], bias: dict[str, dict[date, bool]], p: Params,
             start_equity: float = 100_000.0) -> dict:
    """Walk every coin's bars in time order; signals act at the next bar's open."""
    cost = (p.fee_pct + p.slippage_pct) / 100
    cash, fees = start_equity, 0.0
    book: dict[str, list[Tranche]] = {pair: [] for pair in bars}
    last_price = {pair: None for pair in bars}
    tapes = {pair: _Tape() for pair in bars}
    pending: dict[str, tuple] = {}            # pair -> ("buy", stop, session) | ("exit",)
    entries_today: dict[tuple, int] = {}
    day_start_equity: dict[date, float] = {}
    trades, equity_curve = [], []

    def equity() -> float:
        return cash + sum(t.qty * (last_price[pr] or t.entry) for pr, ts in book.items() for t in ts)

    def sell_all(pair: str, price: float, why: str, when: datetime):
        nonlocal cash, fees
        for t in book[pair]:
            fill = price * (1 - cost)
            fees += t.qty * price * p.fee_pct / 100
            cash += t.qty * fill
            trades.append({"pair": pair, "session": t.session, "exit_time": when.isoformat(), "entry": t.entry,
                           "exit": fill, "qty": t.qty, "pnl": t.qty * (fill - t.entry), "why": why})
        book[pair] = []

    timeline = sorted((b.t, pair, b) for pair, series in bars.items() for b in series)
    for t, pair, bar in timeline:
        day = t.date()
        day_start_equity.setdefault(day, equity())

        # Orders decided on the previous bar fill at this bar's open.
        order = pending.pop(pair, None)
        if order and order[0] == "exit" and book[pair]:
            sell_all(pair, bar.o, "exit signal", t)
        elif order and order[0] == "buy":
            stop, fill = order[1], bar.o * (1 + cost)
            eq = equity()
            if fill > stop:
                risk_qty = eq * p.risk_pct / 100 / (fill - stop)
                exposure = sum(x.qty * (last_price[pr] or x.entry) for pr, ts in book.items() for x in ts)
                room = max(0.0, eq * p.max_exposure_pct / 100 - exposure)
                qty = min(risk_qty, room / fill, cash / fill)
                if qty * fill >= 10:
                    cash -= qty * fill
                    fees += qty * bar.o * p.fee_pct / 100
                    book[pair].append(Tranche(qty, fill, stop, order[2]))

        # Stops trade through intrabar: out at the stop, or the open if it gapped below.
        hit = [x for x in book[pair] if bar.l <= x.stop]
        for x in hit:
            price = min(bar.o, x.stop) * (1 - cost)
            fees += x.qty * min(bar.o, x.stop) * p.fee_pct / 100
            cash += x.qty * price
            trades.append({"pair": pair, "session": x.session, "exit_time": t.isoformat(), "entry": x.entry,
                           "exit": price, "qty": x.qty, "pnl": x.qty * (price - x.entry), "why": "stop"})
        book[pair] = [x for x in book[pair] if x not in hit]

        last_price[pair] = bar.c
        sess = session_of(t)
        tape = tapes[pair]
        vwap = tape.update(bar, sess, p)
        if vwap is not None:
            if book[pair] and exit_signal(tape, bar, vwap):
                pending[pair] = ("exit",)
            else:
                stop = entry_signal(tape, bar, vwap, p)
                key = (pair, day)
                adding_to_loser = any(bar.c < x.entry for x in book[pair])
                down_today = equity() <= day_start_equity[day] * (1 - p.daily_loss_pct / 100)
                if (stop is not None and bias.get(pair, {}).get(day) and not adding_to_loser and not down_today
                        and entries_today.get(key, 0) < p.max_entries_per_coin_day
                        and (bar.c - stop) / bar.c * 100 >= p.min_stop_pct):
                    pending[pair] = ("buy", stop, sess[0])
                    entries_today[key] = entries_today.get(key, 0) + 1
                    tape.entered_this_session = True
            tape.traded_above_vwap |= bar.c > vwap
            tape.prev_close_below_vwap = bar.c < vwap
        equity_curve.append((t, equity()))

    final = equity()
    peak, max_dd = start_equity, 0.0
    for _, e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, 1 - e / peak)
    daily_close: dict[date, float] = {}
    for t, e in equity_curve:
        daily_close[t.date()] = e
    days = sorted(daily_close)
    daily_returns = [daily_close[b] / daily_close[a] - 1 for a, b in zip(days, days[1:])]
    wins = [x for x in trades if x["pnl"] > 0]

    def breakdown(key):
        groups = {}
        for x in trades:
            groups.setdefault(x[key], []).append(x)
        return {k: {"exits": len(v), "pnl": round(sum(x["pnl"] for x in v), 2),
                    "win_rate_pct": round(sum(x["pnl"] > 0 for x in v) / len(v) * 100, 1)}
                for k, v in sorted(groups.items())}

    return {
        "params": asdict(p),
        "start": timeline[0][0].isoformat() if timeline else None,
        "end": timeline[-1][0].isoformat() if timeline else None,
        "return_pct": round((final / start_equity - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "worst_day_pct": round(min(daily_returns) * 100, 2) if daily_returns else None,
        "exits": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "fees_paid": round(fees, 2),
        "open_positions_at_end": {pr: len(ts) for pr, ts in book.items() if ts},
        "by_pair": breakdown("pair"),
        "by_session": breakdown("session"),
    }


def buy_and_hold(series: list[Bar]) -> float:
    return round((series[-1].c / series[0].o - 1) * 100, 2) if series else 0.0
