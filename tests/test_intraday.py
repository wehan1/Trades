from datetime import date, datetime, timedelta, timezone

from trades import intraday
from trades.intraday import Bar, Params


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_sessions_follow_daylight_saving():
    # London opens 08:00 local: 07:00 UTC in summer, 08:00 UTC in winter.
    assert intraday.session_of(utc(2026, 7, 1, 7, 0)) == ("london", date(2026, 7, 1))
    assert intraday.session_of(utc(2026, 12, 1, 7, 0)) is None
    assert intraday.session_of(utc(2026, 12, 1, 8, 0)) == ("london", date(2026, 12, 1))
    # New York opens 09:30 local: 13:30 UTC in summer; London still open then.
    assert intraday.session_of(utc(2026, 7, 1, 13, 30))[0] == "london"
    assert intraday.session_of(utc(2026, 7, 1, 16, 0)) == ("new_york", date(2026, 7, 1))
    assert intraday.session_of(utc(2026, 7, 1, 22, 0)) is None


def bars_from(start, closes, spread=1.0, volume=1.0):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append(Bar(start + timedelta(minutes=15 * i), o, max(o, c) + spread, min(o, c) - spread, c, volume, c))
    return out


DAY = date(2026, 12, 1)
OPEN = utc(2026, 12, 1, 8, 0)          # London open, winter


def run(closes, **kw):
    bars = {"BTC/USD": bars_from(OPEN, closes)}
    bias = {"BTC/USD": {DAY: kw.pop("bias", True)}}
    return intraday.backtest(bars, bias, Params(**{"fee_pct": 0, "slippage_pct": 0, **kw}))


def test_breakout_enters_then_exits_on_signal():
    # Opening range 100-101 (+/-1 spread), breakout to 103+, then a slide below EMA and VWAP.
    closes = [100, 101, 103, 105, 107, 109, 111, 113, 100, 95, 94]
    r = run(closes, setup="orb", ema_bars=3)
    assert r["exits"] == 1 and r["return_pct"] != 0
    assert not r["open_positions_at_end"]


def test_no_entries_without_long_bias():
    r = run([100, 101, 103, 105, 107, 109, 100, 95], setup="orb", bias=False)
    assert r["exits"] == 0 and r["return_pct"] == 0


def test_stop_caps_the_loss_at_the_risk_budget():
    # Breakout, then a collapse straight through the range low (the stop).
    closes = [100, 101, 103, 60]
    r = run(closes, setup="orb", risk_pct=1.0, max_exposure_pct=100)
    # Loss is about 1% of equity: the stop gap is the bar's open, not 60.
    assert -1.6 < r["return_pct"] < -0.5


def test_exposure_cap_limits_size():
    closes = [100, 101, 103, 104]
    r = run(closes, setup="orb", risk_pct=50, max_exposure_pct=10)
    assert r["open_positions_at_end"] == {"BTC/USD": 1}
    # 10% of the account in at about 104, marked at 104: equity barely moves.
    assert abs(r["return_pct"]) < 0.2


def test_fees_reduce_returns():
    closes = [100, 101, 103, 105, 107, 109, 111, 113, 100, 95, 94]
    free = run(closes, setup="orb", ema_bars=3)
    paid = run(closes, setup="orb", ema_bars=3, fee_pct=0.25, slippage_pct=0.05)
    assert paid["return_pct"] < free["return_pct"] and paid["fees_paid"] > 0


def test_daily_bias_uses_only_prior_closes():
    daily = [Bar(utc(2026, 1, 1) + timedelta(days=i), 0, 0, 0, c, 0, c) for i, c in enumerate([1, 1, 1, 5, 0])]
    bias = intraday.daily_bias(daily, 3)
    assert bias == {date(2026, 1, 4): False, date(2026, 1, 5): True}


def test_scaled_windows_cover_the_same_clock_time():
    p = intraday.scaled(Params(), 3)
    assert (p.opening_range_bars, p.ema_bars, p.stop_lookback_bars) == (10, 100, 20)
    assert intraday.scaled(Params(), 15) == Params()


def test_rsi_is_100_on_a_steady_rise_and_0_on_a_steady_fall():
    up, down = intraday._Tape(), intraday._Tape()
    for i in range(15):
        up._update_rsi(100 + i, 14)
        down._update_rsi(100 - i, 14)
    assert up.rsi == 100.0 and down.rsi == 0.0
    assert intraday.scaled(Params(), 3).rsi_bars == 70
