"""trades: run the desk, check proposals, score the guardrail, serve the dashboard."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from trades import agents, config, evaluate, execution, jev, pipeline, protect, store
from trades.alpaca import AlpacaError, AlpacaPaper
from trades.assets import crypto_pair
from trades.proposal import Proposal


def _broker(s):
    try:
        return AlpacaPaper(s)
    except AlpacaError as exc:
        print(f"  broker: {exc}")
        return None


def _summary(record: dict) -> str:
    p = record["proposal"]
    line = f"{p['ticker']} {p['side'].upper()} {p['size_pct']}%  ->  {record['decision']}"
    if record["blocked_by"]:
        line += f"  ({record['blocked_by']})"
    probs = (record.get("jev") or {}).get("probabilities")
    if probs:
        line += "\n  Jev: " + ", ".join(f"{q} {v:.0%}" for q, v in probs.items())
    if record.get("execution"):
        line += f"\n  execution: {record['execution']['note']}"
    return line


def cmd_run(args, s):
    """Agent desk -> guardrail -> execution checks -> paper order (if enabled)."""
    cache = jev.Cache(s.data_dir / "jev_cache.json")
    broker = _broker(s)
    for raw in args.tickers:
        ticker = crypto_pair(raw) or raw.upper()
        account = {}
        if broker:
            try:
                account = broker.account_facts(ticker)
            except AlpacaError as exc:
                print(f"  account facts unavailable: {exc}")
        print(f"[{ticker}] running the agent desk for {args.date} (this takes minutes)...")
        proposal, signal, report = agents.run(ticker, args.date, s, account)
        print(f"  rating {signal}; report at {report}")
        if proposal is None:
            store.append(s.data_dir, {"id": f"{ticker}-{args.date}-{signal}", "checked_at": None,
                                      "proposal": None, "decision": "NO TRADE", "signal": signal,
                                      "report": str(report)})
            print("  no trade proposed")
            continue
        record = pipeline.check(proposal, s, cache)
        record["report"] = str(report)
        record["execution"] = execution.run(record, s, broker)
        store.append(s.data_dir, record)
        print("  " + _summary(record))


def cmd_check(args, s):
    raw = json.load(sys.stdin if args.file == "-" else open(args.file, encoding="utf-8"))
    p = Proposal.from_dict({**raw, "source": "pasted"})
    record = pipeline.check(p, s, jev.Cache(s.data_dir / "jev_cache.json"))
    store.append(s.data_dir, record)
    print(_summary(record))


def cmd_evaluate(args, s):
    result = evaluate.run(s, jev.Cache(s.data_dir / "jev_cache.json"))
    summary = result["summary"]
    print(json.dumps(summary, indent=2))
    if summary["unavailable"]:
        print(f"{summary['unavailable']} ideas could not be scored: Jev did not answer.")


def cmd_protect(args, s):
    """Make sure every crypto position has its stop. Run hourly."""
    broker = _broker(s)
    if broker is None:
        return
    for action in protect.run(s, broker, store.load(s.data_dir)):
        done = f"  (orders: {', '.join(action['done'])})" if action["done"] else ""
        print(f"{action['pair']}: {action['note']}{done}")
    if not s.paper_execution:
        print("PAPER_EXECUTION is off: nothing was changed.")


def cmd_backtest(args, s):
    """Both intraday setups, at the given settings, on Alpaca's 15-minute history."""
    from dataclasses import replace as dc_replace
    from datetime import timedelta

    from trades import intraday
    from trades.config import ALPACA_DATA_URL

    broker = _broker(s)
    if broker is None:
        return
    get = lambda path, **params: broker._get(ALPACA_DATA_URL, path, **params)
    cache = s.data_dir / "bars"
    start = datetime.fromisoformat(args.start).date()
    warmup = (start - timedelta(days=90)).isoformat()
    bars, bias, hold = {}, {}, {}
    for pair in s.crypto_universe:
        bars[pair] = intraday.fetch_bars(get, pair, "15Min", args.start, args.end, cache)
        bias[pair] = intraday.daily_bias(intraday.fetch_bars(get, pair, "1Day", warmup, args.end, cache), 50)
        hold[pair] = intraday.buy_and_hold(bars[pair])
        print(f"{pair}: {len(bars[pair])} bars, buy and hold {hold[pair]:+.2f}%")
    settings = {"yours (75% / 3% risk / 6% day)": dict(max_exposure_pct=75, risk_pct=3, daily_loss_pct=6),
                "conservative (30% / 1% / 2%)": dict(max_exposure_pct=30, risk_pct=1, daily_loss_pct=2)}
    results = []
    for setup in intraday.SETUPS:
        for name, values in settings.items():
            r = intraday.backtest(bars, bias, dc_replace(intraday.Params(setup=setup), **values))
            r["name"] = f"{setup} · {name}"
            results.append(r)
            print(f"{r['name']:<45} return {r['return_pct']:+7.2f}%  max drawdown {r['max_drawdown_pct']:5.2f}%  "
                  f"worst day {r['worst_day_pct']:+6.2f}%  exits {r['exits']:4}  win {r['win_rate_pct']}%  "
                  f"fees ${r['fees_paid']:,.0f}")
            for group in ("by_pair", "by_session"):
                print("    " + "  ".join(f"{k}: {v['exits']} exits, ${v['pnl']:+,.0f}, win {v['win_rate_pct']}%"
                                         for k, v in r[group].items()))
    out = s.data_dir / "backtest.json"
    out.write_text(json.dumps({"buy_and_hold_pct": hold, "results": results}, indent=1))
    print(f"saved {out}")


def cmd_performance(args, s):
    try:
        broker = AlpacaPaper(s)
        account = broker.account()
    except (AlpacaError, OSError) as exc:
        print(f"unavailable: {exc}")
        return
    print(f"paper equity {account['equity']}  (yesterday {account['last_equity']})")


def cmd_serve(args, s):
    import uvicorn

    from trades.server import create_app
    uvicorn.run(create_app(s), host=args.host, port=args.port)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trades")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the agent desk on tickers or pairs (BTC/USD), check, and paper-trade if enabled")
    p.add_argument("tickers", nargs="+")
    p.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(),
                   help="analysis date, YYYY-MM-DD (default: today in UTC, crypto's day)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="check a proposal JSON file ('-' for stdin); never executes")
    p.add_argument("file")
    p.set_defaults(func=cmd_check)

    sub.add_parser("evaluate", help="score the guardrail on the labeled test ideas").set_defaults(func=cmd_evaluate)
    sub.add_parser("protect", help="keep every crypto position under its stop (run hourly)").set_defaults(func=cmd_protect)
    p = sub.add_parser("backtest", help="backtest both intraday setups on Alpaca 15-minute crypto history")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.set_defaults(func=cmd_backtest)

    sub.add_parser("performance", help="paper account equity").set_defaults(func=cmd_performance)

    p = sub.add_parser("serve", help="start the dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    args.func(args, config.load())


if __name__ == "__main__":
    main()
