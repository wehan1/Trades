"""Jev's review of a day-trader-skill stock order, before the owner is asked.

Jev reads the whole ticket, not only the reasoning: the bracket order, the
exit plan, the scanner's setup, the entry signal, the time machine's odds,
the news, the sharia verdict and the risk check. It answers approve or reject
with a confidence, plus the three rule checks every proposal gets. A buy goes
to the owner only when Jev approves with at least APPROVE_CONFIDENCE and no
rule check reaches BLOCK_THRESHOLD; stock-send refuses a buy without that
review from today. Sells only exit what is held and are never held up.

The verdict is kept in trader/state/jev.json by order fingerprint, so a
changed order needs a new review.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from trades import jev, stocks
from trades.config import Settings

APPROVE_CONFIDENCE = 0.60
DECISIONS = ("approve", "reject")
# What the day-trader scanner and entry timer can produce (scan.py SETUPS, entry.py).
STOCK_SETUPS = (
    "opening-range breakout: a completed 5-minute bar closing above the high of the first 15 minutes",
    "20-day closing-high breakout",
    "bull flag",
    "reclaimed the 20-day average",
    "oversold bounce (RSI under 30, turning up)",
)
REVIEW_QUESTION = {
    "review": {
        "type": "choice",
        "instructions": "A long-only, cash-only day-trading desk (flat by 15:55 New York, no margin, no "
                        "shorts) proposes `order`. Given `plan`, `setup`, `entry`, `odds`, `news`, `sharia` "
                        "and `risk`, should it take this trade now?",
        "criteria": {
            "approve": "The entry has triggered on one of `allowed_setups`, the stop and target in `plan` "
                       "are concrete, `odds` do not favor the stop, `news` shows no halt, offering, "
                       "earnings surprise against the trade or other event that breaks the setup, "
                       "`sharia` is PASS and `risk` is PASS.",
            "reject": "Any of those fails, the evidence is missing or stale, `odds` favor the stop, or "
                      "`news` gives a concrete reason to stay out.",
        },
    },
}
MAX_FIELD = 4000      # characters per field; Jev reads text, the skills write long files


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _text(value) -> str:
    out = json.dumps(value if value is not None else "not available", ensure_ascii=False, default=str)
    return out if len(out) <= MAX_FIELD else out[:MAX_FIELD] + "...(cut)"


def _about(doc, symbol: str):
    """The part of a skill file about one symbol, when the file covers many."""
    if isinstance(doc, dict):
        for key in ("hits", "symbols", "items", "results"):
            rows = doc.get(key)
            if isinstance(rows, list):
                mine = [r for r in rows if isinstance(r, dict) and str(r.get("symbol", "")).upper() == symbol]
                return mine or None
        if symbol in doc:
            return doc[symbol]
    return doc


def context(order: dict, trader_dir: Path, sharia: dict | None, risk: dict | None) -> dict[str, str]:
    """What Jev reads, as text."""
    symbol = str(order.get("symbol", "")).upper()
    shown = {k: v for k, v in order.items() if k not in ("plan",)}
    return {
        "order": _text(shown),
        "plan": _text(order.get("plan") or _read(trader_dir / "plan.json")),
        "setup": _text(_about(_read(trader_dir / "watchlist.json"), symbol)),
        "entry": _text(_read(trader_dir / "entry.json")),
        "odds": _text(_read(trader_dir / "timemachine.json")),
        "news": _text(_about(_read(trader_dir / "state" / "news.json"), symbol)),
        "sharia": _text(sharia),
        "risk": _text(risk),
        "allowed_setups": "; ".join(STOCK_SETUPS),
        # The three rule checks read `proposal` and `account`.
        "proposal": _text({"ticker": symbol, "side": order.get("side"), "qty": order.get("qty"),
                           "stop": (order.get("plan") or {}).get("stop") or order.get("stop_loss_stop_price"),
                           "reasoning": order.get("why")}),
        "account": _text("cash-only paper account; see `risk` for today's limits"),
    }


def ask(state: dict[str, str], s: Settings, post=requests.post) -> dict:
    if not s.jev_configured:
        raise RuntimeError("Jev not configured (NETWORK_SECRETS=true, or an OpenRouter key)")
    body = {"model": s.jev_model, "state": state, "questions": {**REVIEW_QUESTION, **jev.QUESTIONS}}
    headers = {"Authorization": f"Bearer {s.jev_key}"} if s.jev_key else {}
    response = post(s.jev_url, json=body, headers=headers, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Jev HTTP {response.status_code}")
    payload = response.json()
    answers = payload["answers"]
    choice = answers["review"]
    if choice.get("type") != "choice" or choice.get("choice") not in DECISIONS:
        raise ValueError("Jev returned no review")
    return {"decision": choice["choice"], "confidence": float(choice["confidence"]),
            "rules": {q: float(answers[q]["noul"]) for q in jev.QUESTIONS},
            "model": payload.get("model")}


def verdict(answer: dict, s: Settings) -> tuple[bool, str]:
    flagged = [q for q, p in answer["rules"].items() if p >= s.block_threshold]
    if flagged:
        return False, "Jev flags " + ", ".join(f"{q} {answer['rules'][q]:.0%}" for q in flagged)
    if answer["decision"] != "approve":
        return False, f"Jev rejects ({answer['confidence']:.0%})"
    if answer["confidence"] < APPROVE_CONFIDENCE:
        return False, f"Jev approves at only {answer['confidence']:.0%} (< {APPROVE_CONFIDENCE:.0%})"
    return True, f"Jev approves ({answer['confidence']:.0%})"


def _log(trader_dir: Path) -> Path:
    return trader_dir / "state" / "jev.json"


def review(index: int, trader_dir: Path, s: Settings, broker, now: str | None = None,
           screen=None, post=requests.post) -> dict:
    """Ask Jev about order `index` and record the verdict for today."""
    t = stocks._ticket()
    doc = t.read_json(str(trader_dir / "orders.json"), "orders file")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not 1 <= index <= len(orders):
        raise stocks.SendError(f"there is no order {index} in {trader_dir / 'orders.json'}")
    order = orders[index - 1]
    fp = t.fingerprint(order)
    today = t.to_eastern(t.parse_now(now)).date().isoformat()
    risk, _ = t.risk_verdict(t.read_json(str(trader_dir / "state" / "risk.json"), "risk check", required=False),
                             fp, today)
    try:
        sharia = stocks.sharia_gate(order, broker, screen or stocks._screen_live)
    except Exception as exc:
        sharia = {"verdict": "not PASS", "reason": str(exc)}
    answer = ask(context(order, trader_dir, sharia, risk), s, post)
    ok, why = verdict(answer, s)
    record = {"date": today, "index": index, "symbol": order.get("symbol"), "approved": ok, "why": why, **answer}
    log = _read(_log(trader_dir)) or {}
    if log.get("date") != today:
        log = {"date": today, "results": {}}
    log["results"][fp] = record
    _log(trader_dir).parent.mkdir(parents=True, exist_ok=True)
    _log(trader_dir).write_text(json.dumps(log, indent=1))
    return record


def gate(order: dict, fp: str, trader_dir: Path, today: str) -> None:
    """stock-send's check: a buy needs Jev's approval of this exact order today."""
    if str(order.get("side", "")).lower() != "buy":
        return
    log = _read(_log(trader_dir)) or {}
    rec = (log.get("results") or {}).get(fp) if log.get("date") == today else None
    if not rec:
        raise stocks.SendError("Jev has not reviewed this exact order today (trades stock-jev --index N)")
    if not rec.get("approved"):
        raise stocks.SendError(f"Jev did not approve: {rec.get('why')}")
