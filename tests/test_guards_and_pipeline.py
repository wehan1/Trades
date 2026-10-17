from trades import guards, pipeline
from trades.proposal import Proposal

from .conftest import fixed_jev, unavailable_jev


def proposal(**kw):
    base = dict(ticker="NVDA", side="buy", size_pct=4, stop=224, price=237.1,
                reasoning="Pullback in an uptrend; stop below the 20-day mean.",
                account={"equity": 100000, "day_pnl_pct": 0.2},
                calendar={"date": "2026-10-07"})
    return Proposal.from_dict({**base, **kw})


def failed(p, s):
    return [g.name for g in guards.run(p, s) if not g.passed]


def test_valid_proposal_passes_every_guard(settings):
    assert failed(proposal(), settings) == []


def test_schema_failure_stops_other_guards(settings):
    results = guards.run(proposal(ticker="", side="hold", size_pct="lots"), settings)
    assert [g.name for g in results] == ["schema"]
    assert not results[0].passed


def test_size_counts_existing_position(settings):
    p = proposal(size_pct=3, account={"equity": 100000, "position": {"qty": 10, "market_value": 2500}})
    assert failed(p, settings) == ["position_size"]


def test_adding_to_losing_long_blocks(settings):
    p = proposal(size_pct=1, account={"equity": 100000, "position": {"qty": 10, "market_value": 1000, "unrealized_pl_pct": -4}})
    assert "adding_to_loser" in failed(p, settings)


def test_daily_loss_blocks_buys_but_not_trims(settings):
    acct = {"equity": 100000, "day_pnl_pct": -2.5}
    assert "daily_loss" in failed(proposal(account=acct), settings)
    trim = proposal(side="sell", stop=None, account={**acct, "position": {"qty": 10, "market_value": 2400, "unrealized_pl_pct": 3}})
    assert failed(trim, settings) == []


def test_sell_without_position_is_a_short(settings):
    assert failed(proposal(side="sell", stop=250), settings) == ["no_short"]


def test_stop_on_wrong_side(settings):
    assert failed(proposal(stop=240), settings) == ["stop_side"]


def test_event_restriction(settings):
    assert failed(proposal(calendar={"date": "2026-11-18"}), settings) == ["event_restriction"]
    assert failed(proposal(ticker="AMD", calendar={"date": "2026-11-18"}), settings) == []


def test_numeric_failure_blocks_before_jev(settings):
    asked = []
    def ask(p, s, cache=None):
        asked.append(p)
    record = pipeline.check(proposal(size_pct=50), settings, ask=ask)
    assert record["decision"] == "BLOCK" and record["blocked_by"] == "numeric guard: position_size"
    assert asked == [] and record["jev"] is None


def test_jev_at_threshold_blocks(settings):
    record = pipeline.check(proposal(), settings, ask=fixed_jev(revenge_trading=0.5))
    assert record["decision"] == "BLOCK"
    assert record["blocked_by"].startswith("Jev: revenge_trading")


def test_jev_below_threshold_approves(settings):
    record = pipeline.check(proposal(), settings, ask=fixed_jev(setup_violation=0.49))
    assert record["decision"] == "APPROVE" and record["blocked_by"] is None


def test_jev_unavailable_fails_closed(settings):
    record = pipeline.check(proposal(), settings, ask=unavailable_jev)
    assert record["decision"] == "BLOCK"
    assert record["blocked_by"] == "Jev unavailable (HTTP 503)"


def test_trim_is_judged_on_revenge_only(settings):
    trim = proposal(side="sell", stop=None, account={"equity": 100000, "position": {"qty": 10, "market_value": 2400, "unrealized_pl_pct": 20}})
    record = pipeline.check(trim, settings, ask=fixed_jev(missing_stop=0.96, setup_violation=0.69))
    assert record["decision"] == "APPROVE" and record["jev"]["applied"] == ["revenge_trading"]
    record = pipeline.check(trim, settings, ask=fixed_jev(revenge_trading=0.9))
    assert record["decision"] == "BLOCK"


def test_buys_are_judged_on_all_three(settings):
    record = pipeline.check(proposal(), settings, ask=fixed_jev(missing_stop=0.6))
    assert record["decision"] == "BLOCK" and len(record["jev"]["applied"]) == 3
