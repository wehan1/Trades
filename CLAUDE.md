# Operating this desk with Claude Code

Claude Code is the desk operator. It runs the agents, reads their reports, and keeps the record. It is not a trader. Every order decision belongs to the code path in `trades/pipeline.py` and `trades/execution.py`.

Rules:
- Never place, change or cancel an order outside `trades run`. Never call the Alpaca API directly to trade.
- Never edit thresholds, limits, `ALLOWED_SETUPS`, `config/events.json` or `PAPER_EXECUTION` to get a proposal through. Changes to rules need the owner's explicit say-so and their own commit.
- Never point the broker at a live endpoint. `ALPACA_TRADING_URL` stays the paper URL.
- Report results as they are. A blocked proposal, a failed run or a losing week is reported plainly, and nothing is reworded to look better.
- Keep synthetic evaluation (`trades evaluate`) separate from paper-trading results (`trades performance`, `data/records.jsonl`).

Checks before committing: `pytest -q`.
