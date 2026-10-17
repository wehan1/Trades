"""The agent desk: TradingAgents (on Claude by default) writes the proposal.

TradingAgents ends in a five-tier rating from its Portfolio Manager and a
Trader proposal with optional stop and sizing. This turns that into a
Proposal; a Hold, or a rating nobody could read, is no trade.

Crypto pairs run TradingAgents' crypto pipeline: no fundamentals analyst, the
pair spelled BTC-USD for its data vendors, and BTC as the benchmark its memory
log settles other coins against.
"""

from __future__ import annotations

import os
import re

from trades.assets import is_crypto, yahoo_symbol
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
    network_secrets = os.environ.get("NETWORK_SECRETS", "").strip().lower() in ("true", "1", "yes", "on")
    if network_secrets and not os.environ.get("ANTHROPIC_API_KEY"):
        # api.anthropic.com bypasses the cloud proxy, so its key can't be a
        # network secret. Claude runs through OpenRouter instead, whose key the
        # proxy adds; the client only needs a placeholder to start.
        os.environ.setdefault("TRADINGAGENTS_LLM_PROVIDER", "openrouter")
        os.environ.setdefault("TRADINGAGENTS_DEEP_THINK_LLM", "anthropic/claude-sonnet-5")
        os.environ.setdefault("TRADINGAGENTS_QUICK_THINK_LLM", "anthropic/claude-sonnet-5")
        os.environ.setdefault("OPENROUTER_API_KEY", "set-by-network-secret")
    os.environ.setdefault("TRADINGAGENTS_LLM_PROVIDER", "anthropic")
    if os.environ["TRADINGAGENTS_LLM_PROVIDER"] == "anthropic":
        os.environ.setdefault("TRADINGAGENTS_DEEP_THINK_LLM", "claude-opus-5-5")
        os.environ.setdefault("TRADINGAGENTS_QUICK_THINK_LLM", "claude-sonnet-5")
    try:
        from tradingagents.default_config import build_default_config
    except ImportError as exc:
        raise SystemExit('TradingAgents is not installed: pip install -e ".[agents]"') from exc
    return build_default_config()


STOCK_ANALYSTS = ("market", "social", "news", "fundamentals")
CRYPTO_ANALYSTS = ("market", "social", "news")


def run(ticker: str, trade_date: str, s: Settings, account: dict | None = None):
    """Run the desk once. Returns (proposal or None, signal, report path)."""
    config = configure()
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    crypto = is_crypto(ticker)
    if crypto and yahoo_symbol(ticker) != "BTC-USD":
        config["benchmark_ticker"] = "BTC-USD"
    graph = TradingAgentsGraph(selected_analysts=CRYPTO_ANALYSTS if crypto else STOCK_ANALYSTS, config=config)
    symbol = yahoo_symbol(ticker) if crypto else ticker
    state, signal = graph.propagate(symbol, trade_date, asset_type="crypto" if crypto else "stock")
    report = graph.save_reports(state, symbol)
    state = {**state, "trade_date": trade_date}
    return proposal_from_state(ticker, state, signal, s, account), signal, report
