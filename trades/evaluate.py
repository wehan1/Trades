"""Score the guardrail on the labeled synthetic test ideas.

This measures whether rule-breaking proposals get blocked and valid ones get
through. It says nothing about trading returns, and the ideas are synthetic.
Ideas Jev could not answer are counted as unavailable, never as caught.
"""

from __future__ import annotations

import json
from pathlib import Path

from trades import jev, pipeline
from trades.config import ROOT, Settings
from trades.proposal import Proposal, now_iso

TEST_IDEAS = ROOT / "data" / "test_ideas.json"
JEV_QUESTIONS = tuple(jev.QUESTIONS)


def load_ideas(path: Path = TEST_IDEAS) -> list[Proposal]:
    return [Proposal.from_dict({**raw, "source": "test"}) for raw in json.loads(path.read_text())["ideas"]]


def rule_breaking(label: dict) -> bool:
    return bool(label.get("numeric_guard")) or any(label.get(q) for q in JEV_QUESTIONS)


def score(records: list[dict]) -> dict:
    unavailable = [r for r in records if r["blocked_by"] and r["blocked_by"].startswith("Jev unavailable")]
    scored = [r for r in records if r not in unavailable]
    breaking = [r for r in scored if rule_breaking(r["proposal"]["label"])]
    valid = [r for r in scored if not rule_breaking(r["proposal"]["label"])]

    per_question = {}
    for q in JEV_QUESTIONS:
        hits = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
        for r in scored:
            probs = (r.get("jev") or {}).get("probabilities")
            if not probs:
                continue        # stopped by a numeric guard; Jev was not asked
            said, truth = probs[q] >= 0.5, bool(r["proposal"]["label"].get(q))
            hits[("true_" if said == truth else "false_") + ("positive" if said else "negative")] += 1
        per_question[q] = hits

    return {
        "total": len(records),
        "unavailable": len(unavailable),
        "rule_breaking": len(breaking),
        "caught": sum(r["decision"] == "BLOCK" for r in breaking),
        "valid": len(valid),
        "false_blocks": sum(r["decision"] == "BLOCK" for r in valid),
        "per_question": per_question,
    }


def run(s: Settings, cache: jev.Cache | None = None, ask=jev.ask, path: Path = TEST_IDEAS) -> dict:
    records = [pipeline.check(p, s, cache, ask) for p in load_ideas(path)]
    result = {"evaluated_at": now_iso(), "jev_model": s.jev_model, "summary": score(records), "records": records}
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "evaluation.json").write_text(json.dumps(result, indent=1))
    return result


def last(s: Settings) -> dict | None:
    try:
        return json.loads((s.data_dir / "evaluation.json").read_text())
    except (FileNotFoundError, ValueError):
        return None
