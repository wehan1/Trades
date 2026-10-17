"""Backend for the dashboard. Inspection only: nothing here submits an order."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from trades import config, evaluate, jev, pipeline, store
from trades.alpaca import AlpacaError, AlpacaPaper
from trades.proposal import Proposal

STATIC = Path(__file__).parent / "static"


def create_app(settings: config.Settings | None = None, ask=jev.ask, broker_factory=AlpacaPaper) -> FastAPI:
    s = settings or config.load()
    cache = jev.Cache(s.data_dir / "jev_cache.json")
    app = FastAPI(title="Agent trade check")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        return {
            "jev": {"configured": s.jev_configured, "model": s.jev_model,
                    "endpoint": s.jev_url},
            "alpaca": {"configured": s.alpaca_configured, "mode": "paper"},
            "paper_execution": s.paper_execution,
            "rules": {
                "max_position_pct": s.max_position_pct, "max_daily_loss_pct": s.max_daily_loss_pct,
                "allow_short": s.allow_short, "block_threshold": s.block_threshold,
                "proposal_max_age_min": s.proposal_max_age_min,
                "allowed_setups": list(s.allowed_setups),
                "max_crypto_position_pct": s.max_crypto_position_pct,
                "max_crypto_exposure_pct": s.max_crypto_exposure_pct,
                "max_risk_per_trade_pct": s.max_risk_per_trade_pct,
                "crypto_universe": list(s.crypto_universe),
                "crypto_allowed_setups": list(s.crypto_allowed_setups),
                "event_restrictions": s.restrictions(),
            },
        }

    @app.get("/api/records")
    def records():
        return list(reversed(store.load(s.data_dir)))

    @app.post("/api/check")
    def check(raw: dict):
        """Check a pasted proposal. Recorded, never executed."""
        try:
            p = Proposal.from_dict({**raw, "source": "pasted"})
        except TypeError as exc:
            raise HTTPException(400, str(exc)) from exc
        record = pipeline.check(p, s, cache, ask)
        store.append(s.data_dir, record)
        return record

    @app.get("/api/test-ideas")
    def test_ideas():
        return {"ideas": [p.to_dict() for p in evaluate.load_ideas()], "evaluation": evaluate.last(s)}

    @app.post("/api/evaluate")
    def run_evaluation():
        return evaluate.run(s, cache, ask)

    @app.get("/api/performance")
    def performance():
        """Paper account results straight from Alpaca, or why they are unavailable."""
        try:
            broker = broker_factory(s)
            account = broker.account()
            history = broker.portfolio_history(period="3M", timeframe="1D")
        except AlpacaError as exc:
            return {"available": False, "reason": str(exc)}
        except Exception as exc:  # network refused, DNS, etc.
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"available": True, "account": {
                    "equity": account.get("equity"), "last_equity": account.get("last_equity"),
                    "cash": account.get("cash"), "status": account.get("status")},
                "history": history, "benchmark": _btc_benchmark(broker, history)}

    def _btc_benchmark(broker, history: dict) -> dict:
        """Buying BTC and holding it over the same days: the bar a crypto desk has to clear."""
        stamps = [t for t, e in zip(history.get("timestamp") or [], history.get("equity") or []) if e]
        if not stamps:
            return {"available": False, "reason": "no account history yet"}
        start = datetime.fromtimestamp(stamps[0], timezone.utc).strftime("%Y-%m-%d")
        try:
            closes = broker.daily_closes("BTC/USD", start)
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        if len(closes) < 2:
            return {"available": False, "reason": "not enough BTC history"}
        return {"available": True, "symbol": "BTC/USD", "start": start,
                "return": closes[-1][1] / closes[0][1] - 1}

    return app

