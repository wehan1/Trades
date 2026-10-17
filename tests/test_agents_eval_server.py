from fastapi.testclient import TestClient

from trades import agents, evaluate
from trades.alpaca import AlpacaError
from trades.server import create_app

from .conftest import fixed_jev, unavailable_jev

PLAN = """**Action**: Buy

**Reasoning**: Fundamentals and trend agree; buy the pullback.

**Entry Price**: 236.5

**Stop Loss**: 1,224.75

**Position Sizing**: 4.5% of portfolio

FINAL TRANSACTION PROPOSAL: **BUY**"""


def test_proposal_from_trading_agents_state(settings):
    state = {"trader_investment_plan": PLAN, "final_trade_decision": "Rating: Overweight\nGood.", "trade_date": "2026-10-16"}
    p = agents.proposal_from_state("nvda", state, "Overweight", settings)
    assert (p.ticker, p.side, p.size_pct, p.stop, p.source) == ("NVDA", "buy", 4.5, 1224.75, "agent")
    assert "buy the pullback" in p.reasoning and "Overweight" in p.reasoning
    assert p.calendar == {"date": "2026-10-16"}


def test_missing_levels_use_defaults(settings):
    plan = PLAN.replace("1,224.75", "not provided").replace("4.5% of portfolio", "not provided")
    p = agents.proposal_from_state("NVDA", {"trader_investment_plan": plan}, "Sell", settings)
    assert p.side == "sell" and p.stop is None and p.size_pct == settings.default_size_pct


def test_hold_and_review_are_no_trade(settings):
    for signal in ("Hold", "REVIEW"):
        assert agents.proposal_from_state("NVDA", {}, signal, settings) is None


def test_test_set_labels():
    ideas = evaluate.load_ideas()
    assert len(ideas) == 32
    assert sum(evaluate.rule_breaking(p.label) for p in ideas) == 19
    assert sum(p.asset_class == "crypto" for p in ideas) == 12


def test_evaluation_with_perfect_judge(settings):
    labels = {p.id: p.label for p in evaluate.load_ideas()}

    def oracle(p, s, cache=None):
        from trades.jev import JevResult
        return JevResult({q: 0.9 if labels[p.id][q] else 0.1 for q in evaluate.JEV_QUESTIONS})

    result = evaluate.run(settings, ask=oracle)
    s = result["summary"]
    assert (s["caught"], s["rule_breaking"], s["false_blocks"], s["valid"], s["unavailable"]) == (19, 19, 0, 13, 0)
    crypto = result["by_asset"]["crypto"]
    assert (crypto["caught"], crypto["rule_breaking"], crypto["false_blocks"], crypto["valid"]) == (7, 7, 0, 5)
    for r in result["records"]:            # each numeric label is caught by the rule it names
        if guard := r["proposal"]["label"]["numeric_guard"]:
            assert r["blocked_by"] == f"numeric guard: {guard}"


def test_unanswered_ideas_are_not_counted_as_caught(settings):
    s = evaluate.run(settings, ask=unavailable_jev)["summary"]
    assert s["unavailable"] == 25           # 7 ideas stop at a numeric guard first
    assert s["caught"] == 7 and s["rule_breaking"] == 7


def test_server_check_records_and_never_executes(settings):
    def no_broker(s):
        raise AlpacaError("no Alpaca keys")
    client = TestClient(create_app(settings, ask=fixed_jev(), broker_factory=no_broker))
    r = client.post("/api/check", json={"ticker": "nvda", "side": "buy", "size_pct": 2, "stop": 220,
                                        "price": 230, "reasoning": "trend pullback"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "APPROVE" and body["execution"] is None
    assert client.get("/api/records").json()[0]["id"] == body["id"]
    assert client.get("/api/performance").json() == {"available": False, "reason": "no Alpaca keys"}
    assert client.get("/api/status").json()["paper_execution"] is False
    assert client.get("/").status_code == 200


def test_network_secrets_run_claude_through_openrouter(monkeypatch):
    import os
    # configure() writes to the environment; give it a private copy.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "OPENROUTER_", "TRADINGAGENTS_"))}
    monkeypatch.setattr(os, "environ", {**env, "NETWORK_SECRETS": "true"})
    cfg = agents.configure()
    assert (cfg["llm_provider"], cfg["deep_think_llm"], cfg["quick_think_llm"]) == (
        "openrouter", "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5")
