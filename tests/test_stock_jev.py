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
