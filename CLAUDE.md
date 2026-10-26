# Operating this desk with Claude Code

Claude Code is the desk operator. It runs the agents, reads their reports, and keeps the record. It is not a trader. Every order decision belongs to the code path in `trades/pipeline.py` and `trades/execution.py`.

Rules:
- Never place, change or cancel an order outside `trades run` (crypto) or `trades stock-send` (stocks).
  For an approved stock sell, stock-send (and stock-flatten at the close) cancels that one stock's
  open sell orders (the bracket's stop and target) so the exit can go through; nothing else
  cancels stock orders. Never call the Alpaca API directly to trade.
- Never edit thresholds, limits, `ALLOWED_SETUPS`, `config/events.json` or `PAPER_EXECUTION` to get a proposal through. Changes to rules need the owner's explicit say-so and their own commit.
- Never point the broker at a live endpoint. `ALPACA_TRADING_URL` stays the paper URL.
- Report results as they are. A blocked proposal, a failed run or a losing week is reported plainly, and nothing is reworded to look better.
- Keep synthetic evaluation (`trades evaluate`) separate from paper-trading results (`trades performance`, `data/records.jsonl`).

## Stock day-trading skills (`.claude/skills/trader-*`)

Vendored from day-trader-skill (see `vendor/day-trader-skill/NOTICE.md`). They scan,
plan, size and risk-check US stock trades into `trader/`. Their broker skill expects
Alpaca's MCP server in Claude Desktop, which this cloud setup does not have; instead
`trades stock-send` and `trades stock-auto` send orders to Alpaca paper.

The stock desk is automatic (owner's rule, 2026-10-27: "remove any human decision"). Each
order needs a trader-risk PASS from today for that exact order and Jev's approval of it
(`trades stock-jev --index N`: buys only, at least 60% confidence, no rule check flagged);
`trades stock-auto` then sends it, logged as `approved_by: jev` so it counts toward the day's
limits. It never chases a price more than 25% of the planned risk above the entry. If the owner
declines an order in the session, that decline wins. Never approve on the owner's behalf with
`ticket.py approve`; a typed yes from the owner, when given, is recorded word for word and
followed by stock-send.

The owner's rules for these skills are `config/trader.json` (rules: owner's say-so, own commit).
`trader/` is wiped with the container: start each stock session with
`bash scripts/stock_state.sh restore` and end it with `bash scripts/stock_state.sh save`,
so the daily loss, trade count and day-trade limits carry over.

Stock day trades are flat by the close, decided by Jev (owner's rule, 2026-10-27). At 15:50 New
York the stock desk runs `trades stock-flatten`: for each stock held, Jev answers close or hold;
a clear hold (60%+) keeps the shares under a GTC stop at the bracket's stop price, anything else
sells at market. It only ever exits. Report every close and hold.

Every stock buy must PASS the sharia screen (`trades sharia TICKER`,
`trades/sharia.py`, AAOIFI business and ratio tests from SEC filings); stock-send
enforces it. REVIEW is not a pass. Only the owner decides overrides, in
`config/sharia.json`, in their own commit. Sells are never blocked by the screen.

Checks before committing: `pytest -q`.
