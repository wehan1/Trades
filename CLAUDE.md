# Operating this desk with Claude Code

Claude Code is the desk operator. It runs the agents, reads their reports, and keeps the record. It is not a trader. Every order decision belongs to the code path in `trades/pipeline.py` and `trades/execution.py`.

Rules:
- Never place, change or cancel an order outside `trades run`. Never call the Alpaca API directly to trade.
- Never edit thresholds, limits, `ALLOWED_SETUPS`, `config/events.json` or `PAPER_EXECUTION` to get a proposal through. Changes to rules need the owner's explicit say-so and their own commit.
- Never point the broker at a live endpoint. `ALPACA_TRADING_URL` stays the paper URL.
- Report results as they are. A blocked proposal, a failed run or a losing week is reported plainly, and nothing is reworded to look better.
- Keep synthetic evaluation (`trades evaluate`) separate from paper-trading results (`trades performance`, `data/records.jsonl`).

## Stock day-trading skills (`.claude/skills/trader-*`)

Vendored from day-trader-skill (see `vendor/day-trader-skill/NOTICE.md`). They scan,
plan, size and risk-check US stock trades into `trader/`. Their broker skill expects
Alpaca's MCP server in Claude Desktop, which this cloud setup does not have; instead
`trades stock-send --index N` sends the order to Alpaca paper. It refuses any order
without a trader-risk PASS from today for that exact order and the owner's typed yes
recorded with `ticket.py approve`. Record the yes word for word, never on the owner's
behalf, and run stock-send only right after it.

Checks before committing: `pytest -q`.
