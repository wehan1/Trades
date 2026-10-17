"""Session desk: at the London and New York opens, Haiku 5.5 evaluates each
coin from fresh Alpaca candles, Jev decides buy, sell or hold, and code acts.

  market snapshot (code) -> evaluation (Haiku 5.5, one call per coin)
  -> decision + rule checks (Jev, one call) -> gate (code) -> numeric guards
  and execution checks (code) -> paper order

Jev reads only Haiku's evaluation and the snapshot, so its decision is a
judgment of that case, not an independent look at the market. Code acts only
when Jev picks buy or sell with at least DECISION_CONFIDENCE, and no rule
check that applies reaches the block threshold. "Sell" only ever exits coins
already held: nothing is shorted, nothing bought on margin.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import requests

from trades import execution, jev, pipeline, store
from trades.config import ALPACA_DATA_URL, Settings
from trades.intraday import session_of
from trades.proposal import Proposal, now_iso

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
EVALUATOR_MODEL = "anthropic/claude-haiku-5.5"
DECISION_CONFIDENCE = 0.60
DECISIONS = ("buy", "sell", "hold")


# --- market snapshot --------------------------------------------------------

def _ema(values: list[float], n: int) -> float:
    k, out = 2 / (n + 1), values[0]
    for v in values[1:]:
        out += k * (v - out)
    return out


def snapshot(get, pair: str, now: datetime) -> dict:
    """What the evaluator sees: daily trend, the last day of 15-minute candles
    summarised, and the current session so far. Numbers only, from Alpaca."""
    daily = (get("/v1beta3/crypto/us/bars", symbols=pair, timeframe="1Day",
                 start=(now - timedelta(days=260)).strftime("%Y-%m-%d"), limit=1000) or {}).get("bars", {}).get(pair, [])
    intra = (get("/v1beta3/crypto/us/bars", symbols=pair, timeframe="15Min",
                 start=(now - timedelta(hours=26)).isoformat().replace("+00:00", "Z"), limit=1000) or {}).get("bars", {}).get(pair, [])
    if len(daily) < 50 or len(intra) < 20:
        raise ValueError(f"not enough candles for {pair}: {len(daily)} daily, {len(intra)} 15-minute")
    closes = [float(b["c"]) for b in daily]
    last = float(intra[-1]["c"])
    sess = session_of(now)
    in_session = [b for b in intra
                  if sess and session_of(datetime.fromisoformat(b["t"].replace("Z", "+00:00"))) == sess]
    vol = sum(float(b["v"]) for b in in_session)
    vwap = (sum(float(b["vw"]) * float(b["v"]) for b in in_session) / vol) if vol else None
    opening = in_session[:2]
    return {
        "pair": pair,
        "as_of": now.isoformat(timespec="minutes"),
        "session": sess[0] if sess else None,
        "last_price": last,
        "daily": {
            "sma_50": round(sum(closes[-50:]) / 50, 2),
            "sma_50_five_days_ago": round(sum(closes[-55:-5]) / 50, 2),
            "sma_200": round(sum(closes[-200:]) / min(200, len(closes)), 2),
            "change_1d_pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
            "change_7d_pct": round((closes[-1] / closes[-8] - 1) * 100, 2),
            "high_20d": max(float(b["h"]) for b in daily[-20:]),
            "low_20d": min(float(b["l"]) for b in daily[-20:]),
        },
        "intraday_15m": {
            "ema_20": round(_ema([float(b["c"]) for b in intra], 20), 2),
            "high_24h": max(float(b["h"]) for b in intra),
            "low_24h": min(float(b["l"]) for b in intra),
            "last_8_closes": [float(b["c"]) for b in intra[-8:]],
        },
        "session_so_far": {
            "bars": len(in_session),
            "vwap": round(vwap, 2) if vwap else None,
            "opening_range_high": max(float(b["h"]) for b in opening) if opening else None,
            "opening_range_low": min(float(b["l"]) for b in opening) if opening else None,
        },
    }


# --- evaluation (Haiku) -------------------------------------------------------

EVALUATOR_PROMPT = """You are a crypto day-trading analyst for a long-only, cash-only desk (no shorting,
no margin). At the {session} session open, evaluate {pair} from the market data and account below.

Market data (Alpaca candles):
{market}

Account and position:
{account}

Allowed setups: {setups}

Reply with JSON only, no prose around it:
{{"view": "buy" | "sell" | "hold",
  "setup": "which allowed setup applies, or none",
  "stop": absolute stop price for a buy (below last price), else null,
  "size_pct": percent of account for a buy, else null,
  "reasoning": "3-5 sentences citing the numbers above"}}
"sell" means exit coins already held; with no position, choose buy or hold."""


def evaluate(snap: dict, account: dict, s: Settings, post=requests.post) -> dict:
    prompt = EVALUATOR_PROMPT.format(
        session=snap["session"] or "current", pair=snap["pair"], market=json.dumps(snap, indent=1),
        account=json.dumps(account, indent=1), setups="; ".join(s.crypto_allowed_setups))
    headers = {"Authorization": f"Bearer {s.jev_key}"} if s.jev_key else {}
    response = post(OPENROUTER_CHAT_URL, headers=headers, timeout=90, json={
        "model": EVALUATOR_MODEL, "max_tokens": 700, "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}]})
    if response.status_code != 200:
        raise RuntimeError(f"evaluator HTTP {response.status_code}")
    payload = response.json()
    text = payload["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("evaluator returned no JSON")
    out = json.loads(match.group(0))
    out["view"] = str(out.get("view", "hold")).lower()
    out["usage"] = payload.get("usage")
    return out


# --- decision (Jev) -----------------------------------------------------------

DECISION_QUESTION = {
    "decision": {
        "type": "choice",
        "instructions": "Given `evaluation` of `market` for a long-only, cash-only desk holding `account`, "
                        "what should the desk do with this coin now?",
        "criteria": {
            "buy": "`evaluation` makes a specific, evidence-backed case for buying now, with a stop, "
                   "and `market` supports it.",
            "sell": "The desk holds the coin and `evaluation` gives a specific reason to exit now.",
            "hold": "The case is weak, vague, contradicted by `market`, or calls for waiting; or it says "
                    "sell with no coins held.",
        },
    },
}


def decide(snap: dict, evaluation: dict, account: dict, s: Settings, post=requests.post) -> dict:
    """Jev's decision and the three rule checks, in one request."""
    if not s.jev_configured:
        raise RuntimeError("Jev not configured")
    p = Proposal(ticker=snap["pair"], side="buy", size_pct=evaluation.get("size_pct") or 0,
                 reasoning=evaluation.get("reasoning", ""), stop=evaluation.get("stop"), account=account)
    state = {**jev.state_for(p, s), "market": json.dumps(snap), "evaluation": json.dumps(evaluation)}
    body = {"model": s.jev_model, "state": state, "questions": {**DECISION_QUESTION, **jev.QUESTIONS}}
    headers = {"Authorization": f"Bearer {s.jev_key}"} if s.jev_key else {}
    response = post(s.jev_url, json=body, headers=headers, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Jev HTTP {response.status_code}")
    payload = response.json()
    answers = payload["answers"]
    choice = answers["decision"]
    if choice.get("type") != "choice" or choice.get("choice") not in DECISIONS:
        raise ValueError("Jev returned no decision")
    return {"decision": choice["choice"], "confidence": float(choice["confidence"]),
            "rules": {q: float(answers[q]["noul"]) for q in jev.QUESTIONS},
            "model": payload.get("model"), "usage": payload.get("usage")}


# --- one session ----------------------------------------------------------------

def run_pair(pair: str, s: Settings, broker, now: datetime, post=requests.post) -> dict:
    record = {"id": f"session-{pair.replace('/', '')}-{now:%Y%m%dT%H%M}", "checked_at": now_iso(),
              "source": "session", "pair": pair, "decision": "HOLD", "blocked_by": None,
              "proposal": None, "execution": None}
    get = lambda path, **params: broker._get(ALPACA_DATA_URL, path, **params)
    try:
        account = broker.account_facts(pair)
        snap = snapshot(get, pair, now)
        record["snapshot"] = snap
        evaluation = evaluate(snap, account, s, post)
        record["evaluation"] = evaluation
        verdict = decide(snap, evaluation, account, s, post)
        record["jev_decision"] = verdict
    except Exception as exc:          # anything missing is a hold, recorded, never a guess
        record["blocked_by"] = f"unavailable: {type(exc).__name__}: {exc}"
        return record

    held = float((account.get("position") or {}).get("qty") or 0)
    if verdict["decision"] == "hold":
        record["blocked_by"] = f"Jev: hold ({verdict['confidence']:.0%})"
        return record
    if verdict["confidence"] < DECISION_CONFIDENCE:
        record["blocked_by"] = f"Jev: {verdict['decision']} at {verdict['confidence']:.0%} < {DECISION_CONFIDENCE:.0%}"
        return record
    if verdict["decision"] == "sell" and held <= 0:
        record["blocked_by"] = "Jev: sell with nothing held (no shorting)"
        return record

    if verdict["decision"] == "buy":
        p = Proposal(ticker=pair, side="buy", size_pct=evaluation.get("size_pct") or s.default_size_pct,
                     reasoning=evaluation.get("reasoning", ""), stop=evaluation.get("stop"),
                     price=snap["last_price"], source="session", account=account,
                     calendar={"date": now.strftime("%Y-%m-%d")})
    else:   # exit the whole position: "out of our buys when the indicator says sell"
        value = held * snap["last_price"]
        p = Proposal(ticker=pair, side="sell", size_pct=min(100.0, value / account["equity"] * 100 + 0.01),
                     reasoning=evaluation.get("reasoning", ""), price=snap["last_price"], source="session",
                     account=account, calendar={"date": now.strftime("%Y-%m-%d")})

    # The rule checks came back with the decision; the pipeline reuses them.
    reuse = lambda *_a, **_k: jev.JevResult(verdict["rules"], model=verdict["model"], usage=verdict["usage"])
    checked = pipeline.check(p, s, ask=reuse)
    record.update({k: checked[k] for k in ("proposal", "guards", "jev", "decision", "blocked_by")})
    record["execution"] = execution.run(checked, s, broker)
    return record


def run(s: Settings, broker, pairs=None, now: datetime | None = None, post=requests.post) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    records = [run_pair(pair, s, broker, now, post) for pair in (pairs or s.crypto_universe)]
    for r in records:
        store.append(s.data_dir, r)
    return records
