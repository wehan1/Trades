from dataclasses import replace

from trades import execution, pipeline
from trades.alpaca import AlpacaError
from trades.proposal import Proposal

from .conftest import fixed_jev


class FakeBroker:
    def __init__(self, is_open=True, position=None, price=237.0, buying_power=50000.0):
        self.is_open, self.position, self.price, self.bp = is_open, position, price, buying_power
        self.orders = []

    def clock(self):
        return {"is_open": self.is_open, "next_open": "2026-10-19T13:30:00Z"}

    def account_facts(self, symbol):
        facts = {"equity": 100000.0, "buying_power": self.bp, "day_pnl_pct": 0.1,
                 "status": "ACTIVE", "trading_blocked": False, "recent_trades": []}
        if self.position:
            facts["position"] = self.position
        return facts

    def latest_trade(self, symbol):
        return {"price": self.price, "time": "now"}

    def submit_order(self, order):
        self.orders.append(order)
        return {"id": "order-1", **order}


def approved(settings, **kw):
    p = Proposal(ticker="NVDA", side="buy", size_pct=4, stop=224, price=237.0,
                 reasoning="trend pullback", account={"equity": 100000}, **kw)
    record = pipeline.check(p, settings, ask=fixed_jev())
    assert record["decision"] == "APPROVE"
    return record


def test_paper_execution_off_sends_nothing(settings):
    broker = FakeBroker()
    result = execution.run(approved(settings), settings, broker)
    assert broker.orders == [] and not result["submitted"]
    assert result["order"]["qty"] == "16"          # floor(4000 / 237)
    assert result["order"]["stop_loss"] == {"stop_price": "224.0"}
    assert "PAPER_EXECUTION is off" in result["note"]


def test_paper_execution_on_submits_once_with_proposal_id(settings):
    broker = FakeBroker()
    record = approved(settings)
    result = execution.run(record, replace(settings, paper_execution=True), broker)
    assert result["submitted"] and len(broker.orders) == 1
    assert broker.orders[0]["client_order_id"] == f"trades-{record['id']}"


def test_blocked_record_never_reaches_broker(settings):
    record = pipeline.check(Proposal(ticker="NVDA", side="buy", size_pct=40, reasoning="x"), settings, ask=fixed_jev())
    broker = FakeBroker()
    result = execution.run(record, replace(settings, paper_execution=True), broker)
    assert broker.orders == [] and result["note"] == "blocked before execution"


def test_stale_proposal_not_executed(settings):
    record = approved(settings, as_of="2026-01-01T00:00:00+00:00")
    result = execution.run(record, replace(settings, paper_execution=True), FakeBroker())
    assert not result["submitted"] and result["note"] == "proposal too old to act on"


def test_market_closed(settings):
    result = execution.run(approved(settings), replace(settings, paper_execution=True), FakeBroker(is_open=False))
    assert not result["submitted"] and result["note"] == "market closed"


def test_live_account_guards_rerun(settings):
    losing = {"qty": 10, "market_value": 2000, "avg_entry_price": 260, "unrealized_pl_pct": -8}
    result = execution.run(approved(settings), replace(settings, paper_execution=True), FakeBroker(position=losing))
    assert not result["submitted"] and "adding_to_loser" in result["checks"][-1]["detail"]


def test_live_price_above_stop_side_blocks(settings):
    result = execution.run(approved(settings), replace(settings, paper_execution=True), FakeBroker(price=220))
    assert not result["submitted"] and "stop_side" in result["checks"][-1]["detail"]


def test_no_broker(settings):
    result = execution.run(approved(settings), settings, None)
    assert result["note"] == "no broker"


def test_broker_error_recorded(settings):
    class Failing(FakeBroker):
        def clock(self):
            raise AlpacaError("HTTP 403")
    result = execution.run(approved(settings), settings, Failing())
    assert result["note"] == "broker error" and result["checks"][-1]["detail"] == "HTTP 403"


def test_alpaca_with_network_secrets_sends_no_key_headers(settings):
    from trades.alpaca import AlpacaPaper
    broker = AlpacaPaper(replace(settings, alpaca_key=None, alpaca_secret=None, network_secrets=True))
    assert "APCA-API-KEY-ID" not in broker.http.headers


def test_config_network_secrets(monkeypatch):
    from trades import config
    for key in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NETWORK_SECRETS", "true")
    s = config.load()
    assert s.jev_configured and s.alpaca_configured and s.jev_url == config.OPENROUTER_JEV_URL
    monkeypatch.setenv("NETWORK_SECRETS", "false")
    s = config.load()
    assert not s.jev_configured and not s.alpaca_configured


def test_dust_is_not_a_position(settings):
    # 0.000000219 BTC left after a paper exit made "adding_to_loser" block every later BTC buy.
    from trades.alpaca import AlpacaPaper
    broker = AlpacaPaper(replace(settings, alpaca_key="k", alpaca_secret="s"))
    broker.account = lambda: {"equity": "5099", "last_equity": "5099", "buying_power": "5099",
                              "non_marginable_buying_power": "5099", "crypto_status": "ACTIVE"}
    broker.todays_fills = lambda: []
    dust = {"symbol": "BTCUSD", "asset_class": "crypto", "qty": "0.000000219", "avg_entry_price": "81529",
            "market_value": "0.0178", "unrealized_plpc": "-0.0001"}
    broker.positions = lambda: [dust]
    assert "position" not in broker.account_facts("BTC/USD")
    broker.positions = lambda: [{**dust, "qty": "0.01", "market_value": "815"}]
    assert broker.account_facts("BTC/USD")["position"]["qty"] == 0.01
