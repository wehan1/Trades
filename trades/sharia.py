"""Sharia screen for US stocks, AAOIFI-style, from SEC EDGAR.

Two tests, both from a company's own filings:

1. Business activity, by SEC industry code (SIC): conventional banking,
   lending and insurance, alcohol, tobacco, weapons and the like fail; codes
   that mix permitted and impermissible business (casinos sit under hotels and
   recreation, brokers, REITs, meat packing) go to REVIEW.
2. Financial ratios against market value (shares outstanding x price):
   interest-bearing debt under 30%, cash plus interest-bearing securities
   under 30%, and interest income under 5% of revenue.

A stock trades only on PASS, or on an owner override in config/sharia.json.
Missing figures never pass silently: a ratio that cannot be computed makes the
verdict REVIEW. This is a screen to the AAOIFI thresholds, not a fatwa.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from trades.config import ROOT

DEBT_LIMIT = 0.30
CASH_LIMIT = 0.30
INTEREST_INCOME_LIMIT = 0.05

# (low, high, verdict, reason): SIC ranges, inclusive.
SIC_RULES = (
    (2082, 2085, "FAIL", "alcoholic beverages"),
    (2100, 2199, "FAIL", "tobacco"),
    (3480, 3489, "FAIL", "weapons and ammunition"),
    (5181, 5182, "FAIL", "beer, wine and spirits wholesale"),
    (5921, 5921, "FAIL", "liquor stores"),
    (6000, 6199, "FAIL", "conventional banking and lending"),
    (6300, 6411, "FAIL", "conventional insurance"),
    (2011, 2011, "REVIEW", "meat packing (pork exposure unknown)"),
    (6200, 6299, "REVIEW", "brokers and exchanges (interest income)"),
    (6770, 6770, "REVIEW", "blank-check company (business unknown)"),
    (6798, 6798, "REVIEW", "REIT (tenants and financing unknown)"),
    (7011, 7011, "REVIEW", "hotels and motels (casinos file here)"),
    (7990, 7999, "REVIEW", "amusement and recreation (gambling files here)"),
)

# Interest-bearing debt: total long-term debt (which includes its current part)
# plus short-term borrowings, or, when no total is reported, its two parts.
DEBT_WITH_TOTAL = ("LongTermDebt", "ShortTermBorrowings", "CommercialPaper")
DEBT_IN_PARTS = ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper")
CASH_TAGS = ("CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments", "MarketableSecuritiesCurrent",
             "AvailableForSaleSecuritiesDebtSecuritiesCurrent")
REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
INTEREST_INCOME_TAGS = ("InvestmentIncomeInterest", "InterestIncomeOther", "InterestAndDividendIncomeOperating")

SEC = "https://data.sec.gov"
OVERRIDES = ROOT / "config" / "sharia.json"


class ScreenError(Exception):
    pass


@dataclass
class Verdict:
    ticker: str
    verdict: str                      # PASS, FAIL or REVIEW
    reasons: list = field(default_factory=list)
    company: str | None = None
    sic: str | None = None
    ratios: dict = field(default_factory=dict)
    as_of: str | None = None
    override: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _latest(facts: dict, tag: str, unit: str = "USD") -> dict | None:
    """The most recent reported value for a tag from a 10-K or 10-Q."""
    entries = (facts.get("us-gaap", {}).get(tag) or facts.get("dei", {}).get(tag) or {}).get("units", {}).get(unit, [])
    entries = [e for e in entries if e.get("form", "").startswith(("10-K", "10-Q"))] or entries
    return max(entries, key=lambda e: (e.get("end", ""), e.get("filed", "")), default=None)


def _sum_latest(facts: dict, tags) -> tuple[float | None, str | None]:
    found = [_latest(facts, t) for t in tags]
    found = [f for f in found if f]
    if not found:
        return None, None
    newest = max(f["end"] for f in found)
    # Only figures from the same balance-sheet date add up.
    return float(sum(f["val"] for f in found if f["end"] == newest)), newest


def _annual(facts: dict, tags) -> float | None:
    """The latest full-year (10-K) value of a flow figure, from the first tag reported."""
    for tag in tags:
        entries = (facts.get("us-gaap", {}).get(tag) or {}).get("units", {}).get("USD", [])
        annual = [e for e in entries if e.get("fp") == "FY" and e.get("form", "").startswith("10-K")]
        if annual:
            return float(max(annual, key=lambda e: e["end"])["val"])
    return None


def activity(sic: str | int | None) -> tuple[str, str | None]:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "REVIEW", "industry code not reported"
    for low, high, verdict, reason in SIC_RULES:
        if low <= code <= high:
            return verdict, reason
    return "PASS", None


def screen(ticker: str, submissions: dict, facts: dict, price: float) -> Verdict:
    """The two tests on already-fetched SEC documents and a share price."""
    v = Verdict(ticker=ticker.upper(), verdict="PASS", company=submissions.get("name"),
                sic=str(submissions.get("sic") or "") or None)
    act, why = activity(submissions.get("sic"))
    if act != "PASS":
        v.reasons.append(f"business: {why} (SIC {v.sic or 'none'})")
    verdicts = [act]

    f = facts.get("facts", {})
    shares = _latest(f, "EntityCommonStockSharesOutstanding", "shares")
    if not shares or not price:
        v.reasons.append("market value unknown (no shares outstanding or price)")
        verdicts.append("REVIEW")
    else:
        market_value = float(shares["val"]) * float(price)
        has_total = _latest(f, "LongTermDebt") is not None
        debt, debt_end = _sum_latest(f, DEBT_WITH_TOTAL if has_total else DEBT_IN_PARTS)
        cash, cash_end = _sum_latest(f, CASH_TAGS)
        v.as_of = max(filter(None, (debt_end, cash_end, shares.get("end"))), default=None)
        for name, value, limit in (("debt", debt, DEBT_LIMIT), ("cash", cash, CASH_LIMIT)):
            if value is None:
                v.reasons.append(f"{name} not reported")
                verdicts.append("REVIEW")
                continue
            ratio = value / market_value
            v.ratios[f"{name}_to_market_value"] = round(ratio, 4)
            if ratio >= limit:
                v.reasons.append(f"{name} is {ratio:.1%} of market value (limit {limit:.0%})")
                verdicts.append("FAIL")

    revenue, interest = _annual(f, REVENUE_TAGS), _annual(f, INTEREST_INCOME_TAGS)
    if revenue and interest is not None:
        ratio = interest / revenue
        v.ratios["interest_income_to_revenue"] = round(ratio, 4)
        if ratio >= INTEREST_INCOME_LIMIT:
            v.reasons.append(f"interest income is {ratio:.1%} of revenue (limit {INTEREST_INCOME_LIMIT:.0%})")
            verdicts.append("FAIL")

    v.verdict = "FAIL" if "FAIL" in verdicts else "REVIEW" if "REVIEW" in verdicts else "PASS"
    return _apply_override(v)


def _apply_override(v: Verdict) -> Verdict:
    """The owner's decisions in config/sharia.json win over the computed verdict."""
    try:
        rules = json.loads(OVERRIDES.read_text())
    except (FileNotFoundError, ValueError):
        return v
    for verdict, key in (("PASS", "allow"), ("FAIL", "block")):
        reason = (rules.get(key) or {}).get(v.ticker)
        if reason:
            v.override = f"{key}: {reason}"
            v.verdict = verdict
    return v


# --- fetching -------------------------------------------------------------------

def _get(url: str, get=requests.get) -> dict:
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent:
        raise ScreenError("SEC asks every caller to identify itself: set SEC_USER_AGENT to "
                          "'Your Name you@example.com' in the environment variables")
    response = get(url, headers={"User-Agent": agent}, timeout=30)
    if response.status_code != 200:
        raise ScreenError(f"SEC {url}: HTTP {response.status_code}")
    return response.json()


def cik_for(ticker: str, get=requests.get) -> int:
    table = _get("https://www.sec.gov/files/company_tickers.json", get)
    for row in table.values():
        if row.get("ticker", "").upper() == ticker.upper():
            return int(row["cik_str"])
    raise ScreenError(f"{ticker} is not in SEC's ticker list (not a US-listed filer)")


def fetch_and_screen(ticker: str, price: float, cache_dir: Path, get=requests.get) -> Verdict:
    """Screen with SEC data, cached for the day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache = cache_dir / f"{ticker.upper()}-{today}.json"
    if cache.exists():
        docs = json.loads(cache.read_text())
    else:
        cik = cik_for(ticker, get)
        docs = {"submissions": _get(f"{SEC}/submissions/CIK{cik:010d}.json", get),
                "facts": _get(f"{SEC}/api/xbrl/companyfacts/CIK{cik:010d}.json", get)}
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(docs))
    return screen(ticker, docs["submissions"], docs["facts"], price)
