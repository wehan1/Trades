import json
from dataclasses import replace

import pytest

from trades import config
from trades.jev import JevResult


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for key in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "ALPACA_API_KEY_ID",
                "ALPACA_API_SECRET_KEY", "PAPER_EXECUTION", "JEV_MODEL"):
        monkeypatch.delenv(key, raising=False)
    events = tmp_path / "events.json"
    events.write_text(json.dumps({"restrictions": [
        {"date": "2026-11-18", "tickers": ["NVDA"], "reason": "NVDA earnings"}]}))
    return replace(config.load(), data_dir=tmp_path / "data", events_path=events,
                   jev_url="https://jev.test/v1/systemone", jev_key="k", jev_model="jev-test")


def fixed_jev(**probabilities):
    base = {"setup_violation": 0.05, "revenge_trading": 0.05, "missing_stop": 0.05, **probabilities}

    def ask(p, s, cache=None):
        return JevResult(dict(base), model="jev-test")
    return ask


def unavailable_jev(p, s, cache=None):
    return JevResult(None, error="HTTP 503")
