"""Jev: three independent yes/no questions about one proposal, in one request.

Jev returns a calibrated probability for each question; code compares them to
the block threshold. Jev judges; it never decides.

Request and answer shape follow TypeSafe's System One API as TradingAgents
v0.7.0 uses it (tradingagents/agents/post_screen.py). Check the current schema
at https://docs.typesafe.ai/llms.txt when upgrading.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from trades.config import Settings
from trades.proposal import Proposal

QUESTIONS = {
    "setup_violation": {
        "type": "noul",
        "instructions": (
            "Does `proposal`'s reasoning rely on a tip, rumor or gut feeling instead of "
            "one of `allowed_setups`?"
        ),
        "criteria": {
            "true": "The case rests on a tip, rumor, hype, a hunch or a feeling, or on no "
                    "identifiable setup, rather than on one of `allowed_setups` with evidence.",
            "false": "The case is one of `allowed_setups`, supported by specific evidence.",
        },
    },
    "revenge_trading": {
        "type": "noul",
        "instructions": "Is `proposal` motivated by recovering a loss?",
        "criteria": {
            "true": "The reasoning aims to win back, make up for or recover money lost, or "
                    "reacts to a recent loss by trading bigger or faster.",
            "false": "The trade stands on its own case. A negative daily P&L in `account` "
                     "alone is not revenge trading.",
        },
    },
    "missing_stop": {
        "type": "noul",
        "instructions": "Is a concrete stop or actionable invalidation missing from `proposal`?",
        "criteria": {
            "true": "There is no stop price and no specific, checkable condition that would "
                    "end the trade; vague exits like 'if it looks bad' count as missing.",
            "false": "`proposal` gives a stop price or a specific, checkable invalidation "
                     "such as a close below a stated level.",
        },
    },
}

_RETRY_STATUSES = (429, 529)
_ATTEMPTS = 3
_TIMEOUT = 20.0


@dataclass
class JevResult:
    probabilities: dict[str, float] | None
    error: str | None = None
    model: str | None = None
    cached: bool = False
    usage: dict | None = None    # as returned by the endpoint, when it returns any

    def to_dict(self) -> dict:
        return asdict(self)


def state_for(p: Proposal, s: Settings) -> dict[str, str]:
    """What Jev reads. Values are text, the form TradingAgents sends."""
    proposal = {
        "ticker": p.ticker, "asset_class": p.asset_class, "side": p.side, "size": f"{p.size_pct}% of account",
        "stop": p.stop, "invalidation": p.invalidation or None, "reasoning": p.reasoning,
    }
    return {
        "proposal": json.dumps(proposal, ensure_ascii=False),
        "account": json.dumps(p.account or "not provided", ensure_ascii=False),
        "calendar": json.dumps(p.calendar or {"date": p.trade_date()}, ensure_ascii=False),
        "allowed_setups": "; ".join(s.setups_for(p.ticker)),
    }


class Cache:
    def __init__(self, path: Path):
        self.path = path
        try:
            self._data = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            self._data = {}

    def get(self, key: str):
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        self._data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1))


def ask(p: Proposal, s: Settings, cache: Cache | None = None, post=requests.post) -> JevResult:
    if not s.jev_configured:
        return JevResult(None, error="no Jev key (set OPENROUTER_API_KEY or TYPESAFE_API_KEY)")
    body = {"model": s.jev_model, "state": state_for(p, s), "questions": QUESTIONS}
    key = hashlib.sha256(json.dumps([s.jev_url, body], sort_keys=True).encode()).hexdigest()
    if cache and (hit := cache.get(key)):
        return JevResult(hit["probabilities"], model=hit.get("model"), cached=True, usage=hit.get("usage"))

    headers = {"Authorization": f"Bearer {s.jev_key}"}
    failure, backoff = "no response", 1.0
    for attempt in range(_ATTEMPTS):
        if attempt:
            time.sleep(backoff * random.uniform(0.8, 1.2))
            backoff *= 2
        try:
            response = post(s.jev_url, json=body, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            failure = type(exc).__name__
            continue
        if response.status_code == 200:
            result = parse(response)
            if result.probabilities is not None and cache:
                cache.put(key, {"probabilities": result.probabilities, "model": result.model,
                                "usage": result.usage})
            return result
        failure = f"HTTP {response.status_code}"
        if response.status_code not in _RETRY_STATUSES:
            break
    return JevResult(None, error=failure)


def parse(response) -> JevResult:
    """Read the typed answers directly; anything else is a failure, never a guess."""
    try:
        payload = response.json()
        answers = payload["answers"]
        probabilities = {}
        for name in QUESTIONS:
            answer = answers[name]
            value = float(answer["noul"])
            if answer.get("type") != "noul" or not 0.0 <= value <= 1.0:
                raise ValueError(name)
            probabilities[name] = value
        return JevResult(probabilities, model=payload.get("model"), usage=payload.get("usage"))
    except (ValueError, KeyError, TypeError):
        return JevResult(None, error="malformed response")
