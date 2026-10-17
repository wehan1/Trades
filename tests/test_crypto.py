from dataclasses import replace

from trades import agents, execution, guards, jev, pipeline, protect
from trades.assets import crypto_pair, position_symbol, yahoo_symbol
from trades.proposal import Proposal

from .conftest import fixed_jev


def test_pair_spellings():
    for raw in ("BTC/USD", "btc-usd", "BTCUSD", " eth/usd "):
        assert crypto_pair(raw) in ("BTC/USD", "ETH/USD")
    assert crypto_pair("NVDA") is None and crypto_pair("ABCUSD") is None
    assert position_symbol("BTC/USD") == "BTCUSD" and yahoo_symbol("BTC/USD") == "BTC-USD"
    assert Proposal.from_dict({"ticker": "eth-usd"}).ticker == "ETH/USD"


def crypto(**kw):
    base = dict(ticker="BTC/USD", side="buy", size_pct=8, stop=97500, price=104000,
                reasoning="Pullback to the rising 50-day average.",
                account={"equity": 100000, "day_pnl_pct": 0.1, "crypto_exposure_pct": 0},
                calendar={"date": "2026-10-07"})
    return Proposal.from_dict({**base, **kw})


def failed(p, s):
    return [g.name for g in guards.run(p, s) if not g.passed]


def test_valid_crypto_passes(settings):
    assert failed(crypto(), settings) == []


def test_crypto_rules(settings):
    assert failed(crypto(ticker="DOGE/USD", size_pct=3, stop=0.2, price=0.25), settings) == ["universe"]
    assert failed(crypto(stop=None), settings) == ["stop_required"]
    assert failed(crypto(size_pct=10, stop=80000), settings) == ["risk_per_trade"]
    assert failed(crypto(size_pct=12), settings) == ["position_size"]
    acct = {"equity": 100000, "crypto_exposure_pct": 25}
    assert failed(crypto(account=acct), settings) == ["crypto_exposure"]


def test_crypto_never_shorts(settings):
    s = replace(settings, allow_short=True)
    assert failed(crypto(side="sell", stop=110000), s) == ["no_short"]


def test_jev_gets_crypto_setups(settings):
    state = jev.state_for(crypto(), settings)
    assert state["allowed_setups"] == "; ".join(settings.crypto_allowed_setups)
    assert '"asset_class": "crypto"' in state["proposal"]


class CryptoBroker:
    def __init__(self, price=104000.0, positions=None, fill="filled"):
        self.price, self.fill = price, fill
        self.held = dict(positions or {})          # pair -> qty
        self.orders, self.cancelled, self.open = [], [], []

    def account_facts(self, symbol):
        facts = {"equity": 100000.0, "buying_power": 60000.0, "day_pnl_pct": 0.0, "status": "ACTIVE",
                 "trading_blocked": False, "crypto_exposure_pct": 0.0, "recent_trades": []}
        if symbol in self.held:
            facts["position"] = {"qty": self.held[symbol], "market_value": self.held[symbol] * self.price,
                                 "unrealized_pl_pct": 5.0}
        return facts

    def latest_trade(self, symbol):
        return {"price": self.price, "time": "now"}

    def submit_order(self, order):
        self.orders.append(order)
        if order["type"] == "market" and order["side"] == "buy":
            self.held[order["symbol"]] = self.held.get(order["symbol"], 0) + float(order["qty"]) * 0.9975  # fee in coin
        if order["type"] == "stop_limit":
            self.open.append({"id": f"o{len(self.orders)}", **order})
        return {"id": f"o{len(self.orders)}", **order}

    def wait_for_fill(self, order_id):
        return {"status": self.fill}

    def position(self, symbol):
        return {"qty": str(self.held[symbol]), "current_price": str(self.price)} if self.held.get(symbol) else None

    def positions(self):
        return [{"symbol": position_symbol(p), "asset_class": "crypto", "qty": str(q), "current_price": str(self.price)}
                for p, q in self.held.items()]

    def open_orders(self, symbol):
        return [o for o in self.open if o["symbol"] == symbol]

    def filled_buys(self, symbol):
        return []

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        self.open = [o for o in self.open if o["id"] != order_id]


def approved(settings, **kw):
    record = pipeline.check(crypto(**kw), settings, ask=fixed_jev())
    assert record["decision"] == "APPROVE"
    return record


def test_crypto_buy_places_stop_on_coins_received(settings):
    broker = CryptoBroker()
    result = execution.run(approved(settings), replace(settings, paper_execution=True), broker)
    entry, stop = broker.orders
    assert entry["type"] == "market" and entry["time_in_force"] == "gtc" and "order_class" not in entry
    assert entry["qty"] == "0.076923"                       # 8% of 100k at 104k
    assert result["checks"][2] == {"name": "market_open", "passed": True, "detail": "crypto trades 24/7"}
    assert stop["type"] == "stop_limit" and stop["stop_price"] == "97500.0"
    assert stop["limit_price"] == "96037.5"                 # 1.5% buffer
    assert float(stop["qty"]) <= float(entry["qty"]) * 0.9975 + 1e-9   # fee comes out of the coins


def test_wide_stop_shrinks_size_to_risk_budget(settings):
    broker = CryptoBroker()
    # No price on the proposal (as from the agent desk): risk is first known at the live price.
    result = execution.run(approved(settings, size_pct=10, stop=88400, price=None), settings, broker)
    sizing = next(c for c in result["checks"] if c["name"] == "risk_sizing")
    assert "to 6.66%" in sizing["detail"]                   # 1% / 15% to stop
    assert result["order"]["qty"] == "0.064038"


def test_unfilled_entry_leaves_stop_to_protect(settings):
    broker = CryptoBroker(fill="new")
    result = execution.run(approved(settings), replace(settings, paper_execution=True), broker)
    assert len(broker.orders) == 1 and "run `trades protect`" in result["note"]


def test_protect_places_missing_stop_and_leaves_good_one(settings):
    s = replace(settings, paper_execution=True)
    broker = CryptoBroker(positions={"BTC/USD": 0.05})
    records = [{"proposal": {"ticker": "BTC/USD", "side": "buy", "stop": 97500}, "execution": {"submitted": True}}]
    first = protect.run(s, broker, records)
    assert first[0]["note"] == "no stop order: place one" and len(broker.orders) == 1
    assert protect.run(s, broker, records)[0]["note"] == "stop in place" and len(broker.orders) == 1


def test_protect_sells_when_price_jumped_the_stop(settings):
    s = replace(settings, paper_execution=True)
    broker = CryptoBroker(price=95000, positions={"BTC/USD": 0.05})
    broker.open = [{"id": "s1", "symbol": "BTC/USD", "type": "stop_limit", "side": "sell", "qty": "0.05", "stop_price": "97500"}]
    records = [{"proposal": {"ticker": "BTC/USD", "side": "buy", "stop": 97500}, "execution": {"submitted": True}}]
    action = protect.run(s, broker, records)[0]
    assert "sell at market" in action["note"] and broker.cancelled == ["s1"]
    assert broker.orders[-1]["type"] == "market" and broker.orders[-1]["qty"] == "0.05"


def test_protect_reports_only_when_execution_off(settings):
    broker = CryptoBroker(positions={"BTC/USD": 0.05})
    records = [{"proposal": {"ticker": "BTC/USD", "side": "buy", "stop": 97500}, "execution": {"submitted": True}}]
    assert protect.run(settings, broker, records)[0]["note"] == "no stop order: place one"
    assert broker.orders == []


def test_protect_flags_position_without_stop(settings):
    broker = CryptoBroker(positions={"ETH/USD": 1.0})
    assert protect.run(settings, broker, [])[0]["note"].startswith("no recorded stop")


def test_agent_desk_maps_crypto(settings, monkeypatch):
    calls = {}

    class Graph:
        def __init__(self, selected_analysts, config):
            calls["analysts"], calls["benchmark"] = selected_analysts, config.get("benchmark_ticker")

        def propagate(self, symbol, date, asset_type):
            calls["symbol"], calls["asset_type"] = symbol, asset_type
            return {"trader_investment_plan": "**Stop Loss**: 3,610\\n\\n**Position Sizing**: 5%"}, "Buy"

        def save_reports(self, state, symbol):
            return "report"

    import tradingagents.graph.trading_graph as tg
    monkeypatch.setattr(tg, "TradingAgentsGraph", Graph)
    p, signal, _ = agents.run("ETH/USD", "2026-10-16", settings)
    assert calls == {"analysts": ("market", "social", "news"), "benchmark": "BTC-USD",
                     "symbol": "ETH-USD", "asset_type": "crypto"}
    assert (p.ticker, p.side, p.stop, p.size_pct) == ("ETH/USD", "buy", 3610.0, 5.0)


def test_stop_is_stamped_into_the_entry_order_and_recovered(settings):
    broker = CryptoBroker()
    execution.run(approved(settings), replace(settings, paper_execution=True), broker)
    entry = broker.orders[0]
    assert entry["client_order_id"].endswith("-s97500.0")
    broker.filled_buys = lambda pair: [{"side": "buy", "status": "filled", "client_order_id": entry["client_order_id"]}]
    assert protect.stop_from_orders(broker, "BTC/USD") == 97500.0


def test_protect_uses_the_order_stamp_when_no_log_survived(settings):
    s = replace(settings, paper_execution=True)
    broker = CryptoBroker(positions={"BTC/USD": 0.05})
    broker.filled_buys = lambda pair: [{"side": "buy", "status": "filled", "client_order_id": "trades-abc-s97500"}]
    action = protect.run(s, broker, [])[0]
    assert action["note"] == "no stop order: place one" and broker.orders[-1]["stop_price"] == "97500.0"
