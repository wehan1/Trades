import json

import pytest

from trades import stock_jev, stocks
from trades.config import load
from tests.test_stocks import NOW, ORDER, PASS, Broker, paths, screen_as, ticket, trader  # noqa: F401


class Reply:
    def __init__(self, decision, confidence, rules=None):
        self.status_code = 200
        rules = rules or {}
        self.body = {"model": "jev-test", "answers": {
            "review": {"type": "choice", "choice": decision, "confidence": confidence},
            **{q: {"type": "noul", "noul": rules.get(q, 0.05)} for q in ("setup_violation", "revenge_trading",
                                                                          "missing_stop")}}}

    def json(self):
        return self.body


def jev_says(*args, **kw):
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return Reply(*args, **kw)
    post.sent = sent
    return post


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("NETWORK_SECRETS", "true")
    return load()


def test_jev_reads_the_whole_ticket(trader, s):
    (trader / "timemachine.json").write_text(json.dumps({"p_target": 0.41, "p_stop": 0.33}))
    post = jev_says("approve", 0.8)
    r = stock_jev.review(1, trader, s, Broker(), NOW, screen=PASS, post=post)
    state = post.sent[0]["state"]
    assert {"order", "plan", "setup", "entry", "odds", "news", "sharia", "risk", "proposal"} <= set(state)
    assert "0.41" in state["odds"] and "PASS" in state["sharia"] and "opening-range" in state["allowed_setups"]
    assert r["approved"] and r["why"] == "Jev approves (80%)"


@pytest.mark.parametrize("reply, why", [
    (("reject", 0.9), "Jev rejects"),
    (("approve", 0.55), "only 55%"),
    (("approve", 0.9, {"missing_stop": 0.7}), "missing_stop 70%"),
])
def test_no_approval_blocks_the_send(trader, s, reply, why):
    r = stock_jev.review(1, trader, s, Broker(), NOW, screen=PASS, post=jev_says(*reply))
    assert not r["approved"] and why in r["why"]
    assert ticket("approve", "--index", "1", "--reply", "yes", *paths(trader)).returncode == 0
    broker = Broker()
    with pytest.raises(stocks.SendError, match="Jev did not approve"):
        stocks.send(1, broker, trader, NOW, screen=PASS)
    assert broker.orders == []


def test_unreviewed_or_changed_order_is_refused(trader):
    (trader / "state" / "jev.json").unlink()
    assert ticket("approve", "--index", "1", "--reply", "yes", *paths(trader)).returncode == 0
    with pytest.raises(stocks.SendError, match="not reviewed"):
        stocks.send(1, Broker(), trader, NOW, screen=PASS)


def test_sharia_failure_reaches_jev_as_not_pass(trader, s):
    post = jev_says("reject", 0.95)
    stock_jev.review(1, trader, s, Broker(), NOW, screen=screen_as("FAIL", ["banking"]), post=post)
    assert "not PASS" in post.sent[0]["state"]["sharia"]


def test_sells_need_no_review(trader):
    sell = dict(ORDER, side="sell", order_class="simple")
    for k in ("take_profit_limit_price", "stop_loss_stop_price"):
        sell.pop(k)
    stock_jev.gate(sell, "any", trader, "2026-10-27")     # does not raise


PLANNED = dict(ORDER, plan={"entry": 364.0, "stop": 358.32})


@pytest.fixture
def planned(trader):
    (trader / "orders.json").write_text(json.dumps({"orders": [PLANNED]}))
    fp = stocks._ticket().fingerprint(PLANNED)
    (trader / "state" / "risk.json").write_text(json.dumps(
        {"date": "2026-10-27", "results": [{"fingerprint": fp, "verdict": "PASS", "reasons": []}]}))
    return trader


def reviewed_planned(trader, minutes_ago, approved=True):
    import datetime as dt
    fp = stocks._ticket().fingerprint(PLANNED)
    at = dt.datetime(2026, 10, 27, 14, 20) - dt.timedelta(minutes=minutes_ago)
    (trader / "state" / "jev.json").write_text(json.dumps({"date": "2026-10-27", "results": {
        fp: {"approved": approved, "why": "Jev approves (80%)" if approved else "Jev rejects", "at_utc": at.isoformat()}}}))


def test_unanswered_jev_approved_buy_is_sent_after_the_wait(planned):
    reviewed_planned(planned, 20)
    broker = Broker()                                   # price 364.2: within the chase limit
    rows = stocks.jev_fallback(broker, planned, NOW, screen=PASS)
    assert rows[0]["sent"] and len(broker.orders) == 1
    log = json.loads((planned / "state" / "broker.json").read_text())["orders"]
    assert log[0]["approved_by"] == "jev" and "no answer from the owner" in log[0]["reply"]
    assert log[-1]["status"] == "placed"                # counts toward the day's limits
    assert not stocks.jev_fallback(broker, planned, NOW, screen=PASS)[0]["sent"]   # never twice


@pytest.mark.parametrize("minutes, approved, why", [
    (5, True, "still waiting"),
    (20, False, "did not approve"),
])
def test_no_send_before_the_wait_or_without_approval(planned, minutes, approved, why):
    reviewed_planned(planned, minutes, approved)
    broker = Broker()
    rows = stocks.jev_fallback(broker, planned, NOW, screen=PASS)
    assert not rows[0]["sent"] and why in rows[0]["why"] and broker.orders == []


def test_owner_decline_always_wins(planned):
    reviewed_planned(planned, 20)
    assert ticket("decline", "--index", "1", *paths(planned)).returncode == 0
    broker = Broker()
    rows = stocks.jev_fallback(broker, planned, NOW, screen=PASS)
    assert not rows[0]["sent"] and "already answered" in rows[0]["why"] and broker.orders == []


def test_price_that_ran_away_is_not_chased(planned):
    reviewed_planned(planned, 20)
    broker = Broker()
    broker.latest_trade = lambda symbol: {"price": 366.0}   # entry 364 + 25% of 5.68 = 365.42
    rows = stocks.jev_fallback(broker, planned, NOW, screen=PASS)
    assert not rows[0]["sent"] and "ran past entry" in rows[0]["why"] and broker.orders == []
