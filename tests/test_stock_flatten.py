import pytest

from trades import stock_flatten
from trades.config import load


class Broker:
    def __init__(self, positions, legs=()):
        self.pos = {p["symbol"]: dict(p) for p in positions}
        self.open = [dict(o) for o in legs]
        self.orders, self.canceled = [], []

    def positions(self):
        return list(self.pos.values())

    def position(self, symbol):
        return self.pos.get(symbol)

    def open_orders_with_legs(self, symbol):
        return [o for o in self.open if o["symbol"] == symbol]

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        self.open = [o for o in self.open if o["id"] != order_id]

    def submit_order(self, body):
        self.orders.append(body)
        return {"id": f"o{len(self.orders)}"}


TSLA = {"symbol": "TSLA", "qty": "10", "asset_class": "us_equity", "avg_entry_price": "390",
        "current_price": "401", "unrealized_pl": "110"}
BTC = {"symbol": "BTCUSD", "qty": "0.1", "asset_class": "crypto"}
LEGS = [{"id": "stop-1", "symbol": "TSLA", "side": "sell", "type": "stop", "stop_price": "385.80"},
        {"id": "tp-1", "symbol": "TSLA", "side": "sell", "type": "limit", "limit_price": "410"}]


def jev(decision, confidence, status=200):
    class R:
        status_code = status

        def json(self):
            return {"model": "jev-test", "answers": {"close": {"type": "choice", "choice": decision,
                                                                "confidence": confidence}}}
    return lambda *a, **k: R()


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("NETWORK_SECRETS", "true")
    return load()


def test_jev_close_sells_everything_and_clears_the_legs(s, tmp_path):
    b = Broker([TSLA, BTC], LEGS)
    rows = stock_flatten.run(b, s, tmp_path, post=jev("close", 0.9))
    assert [r["symbol"] for r in rows] == ["TSLA"]                     # crypto untouched
    assert b.canceled == ["stop-1", "tp-1"]
    assert b.orders == [dict(b.orders[0], symbol="TSLA", qty="10", side="sell", type="market")]
    assert (tmp_path / "state" / "flatten.json").exists()


def test_jev_hold_keeps_the_shares_under_a_gtc_stop_at_the_same_price(s, tmp_path):
    b = Broker([TSLA], LEGS)
    rows = stock_flatten.run(b, s, tmp_path, post=jev("hold", 0.8))
    assert rows[0]["action"] == "hold"
    o = b.orders[0]
    assert (o["type"], o["stop_price"], o["time_in_force"], o["qty"]) == ("stop", "385.80", "gtc", "10")


@pytest.mark.parametrize("post, legs, why", [
    (jev("hold", 0.5), LEGS, "only 50%"),
    (jev("hold", 0.9, status=503), LEGS, "unavailable"),
    (jev("hold", 0.9), LEGS[1:], "no stop"),
])
def test_anything_short_of_a_clear_hold_closes(s, tmp_path, post, legs, why):
    b = Broker([TSLA], legs)
    rows = stock_flatten.run(b, s, tmp_path, post=post)
    assert rows[0]["action"] == "close" and why in rows[0]["why"]
    assert b.orders[0]["type"] == "market"


def test_dry_run_sends_nothing(s, tmp_path):
    b = Broker([TSLA], LEGS)
    rows = stock_flatten.run(b, s, tmp_path, post=jev("close", 0.9), execute=False)
    assert rows[0]["action"] == "close" and b.orders == [] and b.canceled == []


def test_flat_account_does_nothing(s, tmp_path):
    b = Broker([BTC])
    assert stock_flatten.run(b, s, tmp_path, post=jev("close", 0.9)) == []
    assert b.orders == []
