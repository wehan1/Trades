# Trades

A paper-trading desk for **crypto** (BTC, ETH, SOL), with stocks still supported. [TradingAgents](https://github.com/TauricResearch/TradingAgents), running on Claude, writes the trade proposals. Code rules and TypeSafe's Jev check them, and approved ones go to **Alpaca paper trading**. The goal is to find out, with fake money, whether the decisions beat simply holding BTC.

The strategy (what it trades, when, entries, risk and exits) is in **[STRATEGY.md](STRATEGY.md)**.

```
Agent proposal + account/calendar
  → numeric guards (code)        fail → BLOCK
  → Jev: 3 yes/no probabilities  any ≥ 0.5, or no answer → BLOCK
  → execution checks (code)      freshness, live account, risk sizing, buying power
  → paper order                  only with PAPER_EXECUTION=true
  → crypto: stop-limit sell      placed once the entry fills; `trades protect` keeps it there
```

Jev judges; code decides. Jev is asked three independent questions in one request:

| Question | Blocks when |
|---|---|
| `setup_violation`: does the reasoning rest on a tip, rumor or gut feeling instead of an allowed setup? | p ≥ 0.5 |
| `revenge_trading`: is it motivated by recovering a loss? (A red day alone is not revenge.) | p ≥ 0.5 |
| `missing_stop`: is a concrete stop or actionable invalidation missing? | p ≥ 0.5 |

**The numeric guards:**
- schema
- crypto universe
- position size (counting what's already held)
- total crypto exposure
- a numeric stop on every crypto entry
- risk per trade (size × distance to stop)
- no adding to a losing position
- the daily loss limit
- stop on the correct side of the price
- no shorting
- the event restrictions in `config/events.json`

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[agents,dev]"     # agents = TradingAgents v0.7.0, pinned
cp .env.example .env               # then paste your keys into .env
```

**Your keys go in `.env`**: the copy you just made, not `.env.example`. It's git-ignored and never leaves your machine. Every slot is labeled:

- `ANTHROPIC_API_KEY`: Claude Opus for the agent desk.
- `OPENROUTER_API_KEY`: Jev through OpenRouter (or `TYPESAFE_API_KEY` from TypeSafe directly).
- `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`: from Alpaca's **Paper Trading** dashboard.

The Alpaca endpoint is hard-coded to paper. Going live takes a code change, on purpose.

## Use

```bash
trades serve                         # dashboard at http://127.0.0.1:8000
trades evaluate                      # score the guardrail on 32 labeled synthetic ideas (12 crypto)
trades check proposal.json           # check one proposal; never executes
trades run BTC/USD ETH/USD SOL/USD   # agent desk → guardrail → execution checks
trades protect                       # every crypto position has its stop
trades performance                   # paper account equity
```

`trades run` sends a paper order only when `PAPER_EXECUTION=true`. With it off (the default), the order that *would* have gone out is recorded. The dashboard never submits orders.

To run it on a schedule, use a machine that stays on. Crypto trades 24/7. Example crontab (times in UTC):

```
15 0 * * *  cd ~/Trades && .venv/bin/trades run BTC/USD ETH/USD SOL/USD
5  * * * *  cd ~/Trades && .venv/bin/trades protect
```

Each `run` takes several minutes and costs one TradingAgents analysis per coin.

## Before you trust any result

- **Guardrail scores aren't returns.** `trades evaluate` measures whether rule-breaking ideas are blocked. The ideas are synthetic, and 32 is a small sample.
- **The benchmark is BTC buy-and-hold.** The dashboard shows it next to the paper account. Losing to it means the desk adds nothing.
- **Paper fills are simulated.** They ignore market impact and use a simplified fill model.
- **It takes many trades to know.** A month of daily runs on three coins is a few dozen trades, not enough to tell skill from luck.
- **Backtests on past dates are contaminated.** The LLMs already know what happened after their training data ends. Only decisions made after the model's release count as evidence.

## Layout

| Path | What |
|---|---|
| `trades/agents.py` | runs TradingAgents (its crypto pipeline for pairs) and turns its rating and trader plan into a proposal (Hold means no trade) |
| `trades/assets.py` | crypto pair spellings: BTC/USD, BTCUSD, BTC-USD |
| `trades/guards.py` | numeric guards |
| `trades/jev.py` | Jev client: one batched request, cached, never guesses on a bad answer |
| `trades/pipeline.py` | guards → Jev → threshold → APPROVE/BLOCK |
| `trades/execution.py` | execution checks, risk sizing, the paper order and the crypto stop |
| `trades/protect.py` | keeps every crypto position under its stop; sells if price gapped through it |
| `trades/alpaca.py` | Alpaca paper REST client (stocks and crypto) |
| `trades/evaluate.py` | test-set scoring by asset class (an unanswered idea is never counted as caught) |
| `trades/server.py`, `trades/static/` | dashboard |
| `data/test_ideas*.json` | labeled synthetic proposals |
| `data/records.jsonl` | every decision (git-ignored) |
