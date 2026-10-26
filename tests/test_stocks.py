import json
import subprocess
import sys

import pytest

from trades import stocks
from trades.sharia import Verdict


def screen_as(verdict, reasons=()):
    return lambda symbol, price: Verdict(ticker=symbol, verdict=verdict, reasons=list(reasons))


PASS = screen_as("PASS")

NOW = "2026-10-27 10:20"          # New York time, inside the session
ORDER = {"symbol": "TSLA", "side": "buy", "qty": 8, "type": "market", "time_in_force": "day",
         "order_class": "bracket", "take_profit_limit_price": 370.04, "stop_loss_stop_price": 358.32,
         "why": "ORB", "plan": {"stop": 358.32}}


class Broker:
    def __init__(self, fail=False):
        self.orders, self.fail = [], fail

    def latest_trade(self, symbol):
        return {"price": 364.2, "time": "now"}

    def submit_order(self, body):
        if self.fail:
            raise RuntimeError("HTTP 403 insufficient buying power")
        self.orders.append(body)
        return {"id": "alp-1", "status": "accepted"}


def ticket(*args):
    return subprocess.run([sys.executable, str(stocks.TICKET), *args, "--now", NOW],
                          capture_output=True, text=True)


@pytest.fixture
def trader(tmp_path):
    d = tmp_path / "trader"
    (d / "state").mkdir(parents=True)
    (d / "orders.json").write_text(json.dumps({"orders": [ORDER]}))
    (d / "config.json").write_text(json.dumps({"paper": True, "broker": "alpaca"}))
    fp = stocks._ticket().fingerprint(ORDER)
    (d / "state" / "risk.json").write_text(json.dumps(
        {"date": "2026-10-27", "results": [{"fingerprint": fp, "verdict": "PASS", "reasons": []}]}))
    (d / "state" / "jev.json").write_text(json.dumps(
        {"date": "2026-10-27", "results": {fp: {"approved": True, "why": "Jev approves (80%)"}}}))
    return d


def paths(d):
    return ["--orders", str(d / "orders.json"), "--config", str(d / "config.json"),
            "--risk", str(d / "state" / "risk.json"), "--state", str(d / "state" / "broker.json")]


def test_no_yes_no_order(trader):
    broker = Broker()
    with pytest.raises(stocks.SendError, match="no yes recorded"):
        stocks.send(1, broker, trader, NOW, screen=PASS)
    assert broker.orders == []


def test_approved_order_goes_out_once_as_a_bracket(trader):
    r = ticket("approve", "--index", "1", "--reply", "yes", *paths(trader))
    assert r.returncode == 0, r.stdout + r.stderr
    broker = Broker()
    out = stocks.send(1, broker, trader, NOW, screen=PASS)
    body = broker.orders[0]
    assert body["symbol"] == "TSLA" and body["qty"] == "8" and body["order_class"] == "bracket"
    assert body["take_profit"] == {"limit_price": "370.04"} and body["stop_loss"] == {"stop_price": "358.32"}
    assert "why" not in body and "plan" not in body and body["client_order_id"].startswith("dts-")
    assert out["response"]["id"] == "alp-1"
    log = json.loads((trader / "state" / "broker.json").read_text())["orders"]
    assert log[-1]["status"] == "placed" and log[-1]["broker_order_id"] == "alp-1"
    with pytest.raises(stocks.SendError):            # recorded as placed: never twice
        stocks.send(1, broker, trader, NOW, screen=PASS)


def test_risk_block_or_stale_check_is_refused(trader):
    risk = json.loads((trader / "state" / "risk.json").read_text())
    risk["results"][0]["verdict"] = "BLOCK"
    (trader / "state" / "risk.json").write_text(json.dumps(risk))
    with pytest.raises(stocks.SendError, match="not passed"):
        stocks.send(1, Broker(), trader, NOW, screen=PASS)


def test_live_config_is_refused(trader):
    (trader / "config.json").write_text(json.dumps({"paper": False}))
    with pytest.raises(stocks.SendError, match="paper orders only"):
        stocks.send(1, Broker(), trader, NOW, screen=PASS)


def test_broker_refusal_is_recorded_as_failed(trader):
    ticket("approve", "--index", "1", "--reply", "yes", *paths(trader))
    with pytest.raises(stocks.SendError, match="refused"):
        stocks.send(1, Broker(fail=True), trader, NOW, screen=PASS)
    log = json.loads((trader / "state" / "broker.json").read_text())["orders"]
    assert log[-1]["status"] == "failed" and "buying power" in log[-1]["note"]


def test_failed_sharia_screen_blocks_the_buy(trader):
    ticket("approve", "--index", "1", "--reply", "yes", *paths(trader))
    broker = Broker()
    with pytest.raises(stocks.SendError, match="FAIL on the sharia screen: business: conventional banking"):
        stocks.send(1, broker, trader, NOW, screen=screen_as("FAIL", ["business: conventional banking and lending"]))
    assert broker.orders == []
    log = json.loads((trader / "state" / "broker.json").read_text())["orders"]
    assert log[-1]["status"] == "failed" and "sharia" in log[-1]["note"]


def test_review_also_blocks_until_the_owner_decides(trader):
    ticket("approve", "--index", "1", "--reply", "yes", *paths(trader))
    with pytest.raises(stocks.SendError, match="REVIEW"):
        stocks.send(1, Broker(), trader, NOW, screen=screen_as("REVIEW", ["debt not reported"]))


def test_sells_are_never_screened(trader):
    sell = {**ORDER, "side": "sell", "order_class": None, "take_profit_limit_price": None,
            "stop_loss_stop_price": None}
    (trader / "orders.json").write_text(json.dumps({"orders": [sell]}))
    fp = stocks._ticket().fingerprint(sell)
    (trader / "state" / "risk.json").write_text(json.dumps(
        {"date": "2026-10-27", "results": [{"fingerprint": fp, "verdict": "PASS", "reasons": []}]}))
    ticket("approve", "--index", "1", "--reply", "yes", *paths(trader))
    broker = Broker()
    stocks.send(1, broker, trader, NOW, screen=screen_as("FAIL", ["would block a buy"]))
    assert broker.orders[0]["side"] == "sell"
