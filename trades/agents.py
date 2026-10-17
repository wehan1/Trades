"""The agent desk: TradingAgents (on Claude by default) writes the proposal.

TradingAgents ends in a five-tier rating from its Portfolio Manager and a
Trader proposal with optional stop and sizing. This turns that into a
Proposal; a Hold, or a rating nobody could read, is no trade.
"""

from __future__ import annotations

import os
import re

from trades.config import Settings
from trades.proposal import Proposal

SIDES = {"Buy": "buy", "Overweight": "buy", "Sell": "sell", "Underweight": "sell"}

_STOP = re.compile(r"\*\*Stop Loss\*\*:\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)")
_SIZING = re.compile(r"\*\*Position Sizing\*\*:\s*(.+)")
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
_REASONING = re.compile(r"\*\*Reasoning\*\*:\s*(.+?)(?:\n\s*\n\*\*|\Z)", re.S)

_REASONING_LIMIT = 6000


def proposal_from_state(ticker: str, state: dict, signal: str, s: Settings,
                        account: dict | None = None) -> Proposal | None:
    side = SIDES.get(signal)
    if side is None:
        return None
    plan = state.get("trader_investment_plan") or ""
    decision = state.get("final_trade_decision") or ""

    stop = None
    if m := _STOP.search(plan):
        stop = float(m.group(1).replace(",", ""))
    size = s.default_size_pct
    if (m := _SIZING.search(plan)) and (pct := _PERCENT.search(m.group(1))):
        size = float(pct.group(1))
    trader = m.group(1).strip() if (m := _REASONING.search(plan)) else plan.strip()

    reasoning = (
        f"Portfolio Manager rating: {signal}.\n\n"
        f"Trader: {trader}\n\n"
        f"Portfolio Manager decision:\n{decision.strip()}"
    )[:_REASONING_LIMIT]
    return Proposal(
        ticker=ticker.upper(), side=side, size_pct=size, reasoning=reasoning,
        stop=stop, source="agent", account=account or {},
        calendar={"date": state.get("trade_date") or ""},
    )


def configure() -> dict:
    """TradingAgents config, on Claude unless the environment says otherwise."""
    os.environ.setdefault("TRADINGAGENTS_LLM_PROVIDER", "anthropic")
    if os.environ["TRADINGAGENTS_LLM_PROVIDER"] == "anthropic":
        os.environ.setdefault("TRADINGAGENTS_DEEP_THINK_LLM", "claude-opus-5-5")
        os.environ.setdefault("TRADINGAGENTS_QUICK_THINK_LLM", "claude-sonnet-5")
    try:
        from tradingagents.default_config import build_default_config
    except ImportError as exc:
        raise SystemExit('TradingAgents is not installed: pip install -e ".[agents]"') from exc
    return build_default_config()


def run(ticker: str, trade_date: str, s: Settings, account: dict | None = None):
    """Run the desk once. Returns (proposal or None, signal, report path)."""
    config = configure()
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph(config=config)
    state, signal = graph.propagate(ticker, trade_date)
    report = graph.save_reports(state, ticker)
    state = {**state, "trade_date": trade_date}
    return proposal_from_state(ticker, state, signal, s, account), signal, report
