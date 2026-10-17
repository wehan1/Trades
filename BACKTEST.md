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
