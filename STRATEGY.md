# Crypto strategy

This is a hypothesis to test on paper, not a known edge. The bar it has to clear is **buying BTC and holding it** over the same days. If it can't beat that after costs, it adds nothing.

## What it trades
- **BTC/USD, ETH/USD and SOL/USD only** (`CRYPTO_UNIVERSE`). They're the most liquid pairs and their news is the most reported. No memecoins and no thin coins.
- **Long only, with no leverage.** Alpaca doesn't short crypto, and leverage turns a bad week into a wipe-out.

## When
- **One decision per coin per day**, after the 00:00 UTC daily close: `trades run BTC/USD ETH/USD SOL/USD` at about 00:15 UTC.
- **`trades protect` every hour.** Crypto never closes, so stops have to be watched around the clock.
- Running more often mostly buys fees. Every round trip pays Alpaca's crypto fee twice, and the spread.

## Entries (the allowed setups Jev checks against)
1. A pullback to the rising 20- or 50-day average **while price holds above its 200-day average**, so it only buys dips in an uptrend.
2. A daily close breaking out of a range of at least two weeks, on rising volume.
3. A flow or news thesis that cites **specific reported figures**: ETF net flows, exchange reserves, a dated protocol upgrade.

Tips, influencer calls, FOMO ("it's ripping") and attempts to win back a loss get blocked.

## Risk (enforced by code, not judgment)
| Rule | Default |
|---|---|
| Numeric stop price on every entry | required |
| Loss if stopped out | ≤ 1% of account; larger sizes are cut to fit |
| One coin | ≤ 10% of account |
| All crypto together | ≤ 30% of account (the coins move together) |
| Day P&L at −2% | no new buys that day |
| Adding to a losing position | blocked |

Example: BTC at 104,000 with a stop at 97,500 is 6.25% away. 1% ÷ 6.25% = 16%, so the 10% per-coin cap binds and the position is 10%. A stop 15% away would allow only about 6.7%.

## Exits
- **The stop** is a GTC stop-limit sell placed as soon as the entry fills. It may fill down to 1.5% below the stop price. If price gaps through that, `trades protect` sells at market on its next hourly run.
- **A Sell or Underweight rating** from the desk trims or closes the position. Sells are always allowed, even on a losing day.

## How to judge it
- Compare the paper account with BTC buy-and-hold over the same period (shown on the dashboard).
- Count only decisions made after the models' release dates. On older dates the models may already know what happened next.
- Wait for 100+ closed trades before drawing conclusions. A month of daily runs on three coins is closer to 20–40 trades.
