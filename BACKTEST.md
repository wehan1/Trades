# Intraday backtest, 17 Apr – 17 Oct 2026

Alpaca 15-minute crypto candles (BTC/USD, ETH/USD, SOL/USD), London and New
York sessions only, long only, cash only. Run with `trades backtest --start
2026-04-17 --end 2026-10-17`. Rules were fixed before the run, nothing was
tuned to it.

**Buy and hold over the same period:** BTC +8.48%, ETH +3.05%, SOL +17.03%.

## Return by cost level

Each cell is the return and max drawdown. "Yours" = 75% max exposure, 3% risk
per entry, 6% daily stop. "Conservative" = 30% / 1% / 2%. Stops tighter than
the minimum are skipped.

| Setup | Min stop | Costs per side | Yours | Conservative |
|---|---|---|---|---|
| Opening-range breakout | 0.15% | Alpaca market orders (0.25% + 0.05% slippage) | −67.9% (DD 68.2%) | −36.4% (DD 36.6%) |
| | | Alpaca limit orders (0.15% + 0.02%) | −47.5% | −22.6% |
| | | Binance level (0.10% + 0.02%) | −36.8% | −16.6% |
| | | none (theoretical) | −1.1% | −0.3% |
| | 0.75% | Alpaca market orders | −48.7% | −23.3% |
| | | Alpaca limit orders | −31.1% | −13.6% |
| | | Binance level | −22.8% | −9.6% |
| | | none | +1.6% | +0.8% |
| VWAP reclaim | 0.15% | Alpaca market orders | −76.0% (DD 76.5%) | −43.3% (DD 43.8%) |
| | | Alpaca limit orders | −52.4% | −25.3% |
| | | Binance level | −38.5% | −17.2% |
| | | none | +16.4% | +6.7% |
| | 0.75% | Alpaca market orders | −43.8% | −20.1% |
| | | Alpaca limit orders | −29.0% | −12.4% |
| | | Binance level | −22.1% | −9.2% |
| | | none | −2.8% | −1.0% |

## What it says

- Costs decide the result. A median 15-minute candle spans 0.22% (BTC) to 0.37%
  (SOL) high to low, while a round trip costs 0.24–0.60%. Before costs the
  setups are near break-even; after costs every real fee level loses.
- Your settings (75% / 3%) multiply the losses roughly two to three times
  versus conservative ones; they do not change the sign.
- No breakdown by coin or session turned positive at Alpaca's fees. London
  lost more than New York in both setups.

## Limits of this test

One six-month window; Alpaca's own crypto market is thin, so its candles carry
wicks a deeper market would not; the daily long filter is a rule standing in
for the AI desk, which cannot be backtested honestly.

# 3-minute candles and an RSI filter, same window

Same rules and window, run with `trades backtest --start 2026-04-17 --end
2026-10-17 --minutes 3` and `--rsi`. On 3-minute candles every window covers the
same clock time as on 15-minute ones (30-minute opening range, 100-bar EMA,
20-bar stop lookback, 70-bar RSI). The RSI rule was fixed before the run: enter
only while 50 < RSI(14) < 70. The 15-minute baseline reproduces the table above.

Return at Alpaca market-order costs (0.25% fee + 0.05% slippage per side), then
before any costs (conservative settings):

| Setup | Candles | RSI | Yours | Conservative | Conservative, no costs |
|---|---|---|---|---|---|
| Opening-range breakout | 15m | off | −67.9% | −36.4% | −0.3% |
| | 15m | on | −68.5% | −36.9% | −2.7% |
| | 3m | off | −71.9% | −39.7% | −2.0% |
| | 3m | on | −71.1% | −39.0% | −2.6% |
| VWAP reclaim | 15m | off | −76.0% | −43.3% | +6.7% |
| | 15m | on | −65.8% | −34.2% | +5.5% |
| | 3m | off | −85.7% | −54.4% | +3.9% |
| | 3m | on | −76.4% | −43.9% | +2.5% |

## What it says

- 3-minute candles lose more than 15-minute ones in every row: more noise per
  trade and the same fee each time. Win rates fall to 5–10%.
- RSI helps after costs only because it skips trades (fewer fees). Before costs
  it lowers the return of every setup, so it filters out more good entries than
  bad ones.
- Nothing beats buying and holding over the same six months.
