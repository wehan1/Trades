import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from trades import session

NOW = datetime(2026, 10, 19, 7, 30, tzinfo=timezone.utc)      # London, 30 min after the open


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


def bars(n, step, start, price):
    return [{"t": (start + step * i).isoformat().replace("+00:00", "Z"), "o": price, "h": price + 50,
             "l": price - 50, "c": price + i, "v": 1.0, "vw": price + i} for i in range(n)]


class Broker:
    def __init__(self, held=0.0):
        self.held, self.orders = held, []

    def _get(self, base, path, **params):
        if params["timeframe"] == "1Day":
            return {"bars": {params["symbols"]: bars(220, timedelta(days=1), NOW - timedelta(days=221), 100000)}}
        return {"bars": {params["symbols"]: bars(100, timedelta(minutes=15), NOW - timedelta(minutes=1500), 104000)}}

    def account_facts(self, symbol):
        facts = {"equity": 100000.0, "buying_power": 100000.0, "day_pnl_pct": 0.0, "status": "ACTIVE",
                 "trading_blocked": False, "crypto_exposure_pct": 0.0, "recent_trades": []}
        if self.held:
            facts["position"] = {"qty": self.held, "market_value": self.held * 104100, "unrealized_pl_pct": 2.0}
        return facts

    def latest_trade(self, symbol):
        return {"price": 104100.0, "time": "now"}

    def submit_order(self, order):
        self.orders.append(order)
        return {"id": "o1"}

    def wait_for_fill(self, oid):
        return {"status": "filled"}

    def position(self, symbol):
        return None

    def open_orders(self, symbol):
        return []


def poster(view, decision, confidence, rules=None):
    rules = rules or {"setup_violation": 0.1, "revenge_trading": 0.05, "missing_stop": 0.05}
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if url == session.OPENROUTER_CHAT_URL:
            content = '{"view": "%s", "setup": "pullback", "stop": 103000, "size_pct": 5, "reasoning": "above vwap"}' % view
            return Resp(200, {"choices": [{"message": {"content": content}}], "usage": {"cost": 0.002}})
        answers = {"decision": {"type": "choice", "choice": decision, "confidence": confidence},
                   **{q: {"type": "noul", "noul": v} for q, v in rules.items()}}
        return Resp(200, {"answers": answers, "model": "jev-test"})
    post.calls = calls
    return post


def run(settings, post, broker=None, **kw):
    s = replace(settings, **kw)
    return session.run_pair("BTC/USD", s, broker or Broker(), NOW, post)


def test_snapshot_reads_the_session():
    snap = session.snapshot(lambda path, **p: Broker()._get(None, path, **p), "BTC/USD", NOW)
    assert snap["session"] == "london" and snap["session_so_far"]["bars"] == 2
    assert snap["session_so_far"]["opening_range_high"] is not None


def test_confident_buy_reaches_the_order_check(settings):
    post = poster("buy", "buy", 0.8)
    r = run(settings, post)
    assert r["decision"] == "APPROVE" and r["proposal"]["side"] == "buy"
    assert post.calls == [session.OPENROUTER_CHAT_URL, settings.jev_url]       # one Jev call, not two
    assert r["execution"]["note"].startswith("all checks passed; PAPER_EXECUTION is off")


def test_paper_order_goes_out_when_enabled(settings):
    broker = Broker()
    r = run(settings, poster("buy", "buy", 0.8), broker, paper_execution=True)
    assert r["execution"]["submitted"] and broker.orders[0]["side"] == "buy"


def test_low_confidence_is_a_hold(settings):
    r = run(settings, poster("buy", "buy", 0.55))
    assert r["decision"] == "HOLD" and "55% < 60%" in r["blocked_by"] and r["proposal"] is None


def test_rule_check_blocks_a_confident_buy(settings):
    r = run(settings, poster("buy", "buy", 0.9, {"setup_violation": 0.7, "revenge_trading": 0, "missing_stop": 0}))
    assert r["decision"] == "BLOCK" and r["blocked_by"].startswith("Jev: setup_violation")


def test_sell_exits_the_whole_position_and_never_shorts(settings):
    r = run(settings, poster("sell", "sell", 0.8))
    assert r["blocked_by"] == "Jev: sell with nothing held (no shorting)"
    broker = Broker(held=0.2)
    r = run(settings, poster("sell", "sell", 0.8), broker, paper_execution=True)
    assert r["decision"] == "APPROVE" and broker.orders[0]["side"] == "sell"
    assert float(broker.orders[0]["qty"]) == 0.2


def test_failures_are_recorded_holds(settings):
    def broken(url, **k):
        return Resp(503, {})
    r = run(settings, broken)
    assert r["decision"] == "HOLD" and r["blocked_by"].startswith("unavailable: RuntimeError")
