# Trades

A paper-trading desk: [TradingAgents](https://github.com/TauricResearch/TradingAgents) (running on Claude) writes trade proposals, code rules and TypeSafe's Jev check them, and approved ones go to **Alpaca paper trading**. The goal is to find out, with fake money, whether the decisions are worth anything.

```
Agent proposal + account/calendar
  → numeric guards (code)        fail → BLOCK
  → Jev: 3 yes/no probabilities  any ≥ 0.5, or no answer → BLOCK
  → execution checks (code)      freshness, live account, size, buying power
  → paper order                  only with PAPER_EXECUTION=true
```

Jev judges; code decides. Jev is asked three independent questions in one request:

| Question | Blocks when |
|---|---|
| `setup_violation`: does the reasoning rest on a tip, rumor or gut feeling instead of an allowed setup? | p ≥ 0.5 |
| `revenge_trading`: is it motivated by recovering a loss? (A red day alone is not revenge.) | p ≥ 0.5 |
| `missing_stop`: is a concrete stop or actionable invalidation missing? | p ≥ 0.5 |

The numeric guards are: schema, max position size (counting what's already held), no adding to a losing position, the daily loss limit, the stop on the correct side of the price, no shorting unless allowed, and the event restrictions in `config/events.json`.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[agents,dev]"     # agents = TradingAgents v0.7.0, pinned
cp .env.example .env               # then fill in keys
```

Keys go in `.env` (git-ignored) and stay on the backend:

- `ANTHROPIC_API_KEY`: the agent desk.
- `OPENROUTER_API_KEY` or `TYPESAFE_API_KEY`: Jev.
- `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`: from a **paper** account at alpaca.markets.

The Alpaca endpoint is hard-coded to paper. Going live takes a code change, on purpose.

## Use

```bash
trades serve                 # dashboard at http://127.0.0.1:8000
trades evaluate              # score the guardrail on 20 labeled synthetic ideas
trades check proposal.json   # check one proposal; never executes
trades run NVDA AAPL         # agent desk → guardrail → execution checks
trades performance           # paper account equity
```

`trades run` sends a paper order only when `PAPER_EXECUTION=true`. With it off (the default), the order that *would* have gone out is recorded. The dashboard never submits orders.

To trade daily, schedule `trades run <tickers>` after the US market opens on a machine that stays on (cron, Task Scheduler, or a small VM). Each run takes several minutes and costs one TradingAgents analysis per ticker.

## Before you trust any result

- **Guardrail scores aren't returns.** `trades evaluate` measures whether rule-breaking ideas are blocked. The ideas are synthetic, and 20 is a small sample.
- **Paper fills are simulated.** They ignore market impact and use a simplified fill model.
- **It takes many trades to know.** A few weeks of daily runs on a handful of tickers is tens of trades, not enough to tell skill from luck. Compare against just holding the benchmark (SPY) over the same period.
- **Backtests on past dates are contaminated.** The LLMs already know what happened after their training data ends. Only decisions made on dates after the model's release count as evidence.

## Layout

| Path | What |
|---|---|
| `trades/agents.py` | runs TradingAgents and turns its rating and trader plan into a proposal (Hold means no trade) |
| `trades/guards.py` | numeric guards |
| `trades/jev.py` | Jev client: one batched request, cached, never guesses on a bad answer |
| `trades/pipeline.py` | guards → Jev → threshold → APPROVE/BLOCK |
| `trades/execution.py` | execution checks and the paper order |
| `trades/alpaca.py` | Alpaca paper REST client |
| `trades/evaluate.py` | test-set scoring (an unanswered idea is never counted as caught) |
| `trades/server.py`, `trades/static/` | dashboard |
| `data/test_ideas.json` | labeled synthetic proposals |
| `data/records.jsonl` | every decision (git-ignored) |
