"""Proposal -> numeric guards -> Jev -> threshold rule -> execution checks.

Every step is recorded, and model judgments are kept apart from code decisions.
"""

from __future__ import annotations

from trades import guards, jev
from trades.config import Settings
from trades.proposal import Proposal, now_iso


def check(p: Proposal, s: Settings, cache: jev.Cache | None = None, ask=jev.ask) -> dict:
    """Decide APPROVE or BLOCK. Approval means the proposal may go on to the
    execution checks, not that an order was sent."""
    record = {
        "id": p.id,
        "checked_at": now_iso(),
        "proposal": p.to_dict(),
        "guards": [],
        "jev": None,
        "decision": "BLOCK",
        "blocked_by": None,
        "execution": None,
    }
    results = guards.run(p, s)
    record["guards"] = [g.to_dict() for g in results]
    failed = [g for g in results if not g.passed]
    if failed:
        record["blocked_by"] = f"numeric guard: {failed[0].name}"
        return record

    answer = ask(p, s, cache)
    record["jev"] = answer.to_dict()
    if answer.probabilities is None:
        # Fail closed: an unanswered check is not a passed one.
        record["blocked_by"] = f"Jev unavailable ({answer.error})"
        return record

    over = {q: v for q, v in answer.probabilities.items() if v >= s.block_threshold}
    if over:
        worst = max(over, key=over.get)
        record["blocked_by"] = f"Jev: {worst} {over[worst]:.0%} >= {s.block_threshold:.0%}"
        return record

    record["decision"] = "APPROVE"
    return record
