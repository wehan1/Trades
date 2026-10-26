import json

import pytest

from trades import sharia


def fact(val, end="2026-06-30", form="10-Q", fp="Q2", filed="2026-07-30"):
    return {"val": val, "end": end, "form": form, "fp": fp, "filed": filed}


def company(sic="3571", shares=1_000_000_000, debt=None, ltd_parts=None, cash=50e9, revenue=400e9, interest=None):
    gaap = {}
    if debt is not None:
        gaap["LongTermDebt"] = {"units": {"USD": [fact(debt)]}}
    for tag, v in (ltd_parts or {}).items():
        gaap[tag] = {"units": {"USD": [fact(v)]}}
    if cash is not None:
        gaap["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": [fact(cash)]}}
    gaap["Revenues"] = {"units": {"USD": [fact(revenue, end="2025-12-31", form="10-K", fp="FY")]}}
    if interest is not None:
        gaap["InvestmentIncomeInterest"] = {"units": {"USD": [fact(interest, end="2025-12-31", form="10-K", fp="FY")]}}
    facts = {"facts": {"us-gaap": gaap, "dei": {"EntityCommonStockSharesOutstanding":
                                                  {"units": {"shares": [fact(shares)]}}}}}
    return {"name": "Test Co", "sic": sic}, facts


@pytest.fixture(autouse=True)
def no_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(sharia, "OVERRIDES", tmp_path / "sharia.json")


def test_clean_company_passes():
    v = sharia.screen("TEST", *company(debt=2e9, cash=1e9), price=20.0)        # market value 20bn
    assert v.verdict == "PASS", v.reasons
    assert v.ratios == {"debt_to_market_value": 0.1, "cash_to_market_value": 0.05}


def test_high_debt_fails():
    v = sharia.screen("TEST", *company(debt=70e9, cash=10e9), price=200.0)      # 35% of 200bn
    assert v.verdict == "FAIL" and "debt is 35.0%" in v.reasons[0]


def test_high_cash_fails():
    v = sharia.screen("TEST", *company(debt=1e9, cash=65e9), price=200.0)
    assert v.verdict == "FAIL" and any("cash is 32.5%" in r for r in v.reasons)


def test_debt_total_is_not_double_counted():
    # LongTermDebt already includes its current part: only the total counts.
    submissions, facts = company(debt=50e9, ltd_parts={"LongTermDebtCurrent": 20e9}, cash=1e9)
    v = sharia.screen("TEST", submissions, facts, price=200.0)
    assert v.ratios["debt_to_market_value"] == 0.25 and v.verdict == "PASS"


def test_debt_from_parts_when_no_total():
    submissions, facts = company(debt=None, ltd_parts={"LongTermDebtNoncurrent": 40e9, "LongTermDebtCurrent": 25e9}, cash=1e9)
    v = sharia.screen("TEST", submissions, facts, price=200.0)
    assert v.ratios["debt_to_market_value"] == 0.325 and v.verdict == "FAIL"


@pytest.mark.parametrize("sic,expected", [("6021", "FAIL"), ("6311", "FAIL"), ("2085", "FAIL"),
                                          ("2111", "FAIL"), ("3482", "FAIL"), ("7011", "REVIEW"),
                                          ("6798", "REVIEW"), ("2080", "PASS"), ("7372", "PASS")])
def test_business_activity(sic, expected):
    v = sharia.screen("TEST", *company(sic=sic, debt=1e9, cash=1e9), price=200.0)
    assert v.verdict == expected


def test_interest_income_over_five_percent_fails():
    v = sharia.screen("TEST", *company(debt=1e9, cash=1e9, revenue=100e9, interest=6e9), price=200.0)
    assert v.verdict == "FAIL" and any("interest income is 6.0%" in r for r in v.reasons)


def test_missing_figures_are_review_not_pass():
    v = sharia.screen("TEST", *company(debt=None, cash=None), price=200.0)
    assert v.verdict == "REVIEW" and "debt not reported" in v.reasons
    v = sharia.screen("TEST", *company(debt=1e9), price=None)
    assert v.verdict == "REVIEW"


def test_owner_overrides(tmp_path, monkeypatch):
    path = tmp_path / "sharia.json"
    path.write_text(json.dumps({"allow": {"TEST": "checked tenants"}, "block": {"KO2": "no"}}))
    monkeypatch.setattr(sharia, "OVERRIDES", path)
    v = sharia.screen("TEST", *company(sic="6798", debt=1e9, cash=1e9), price=200.0)
    assert v.verdict == "PASS" and v.override == "allow: checked tenants"
    v = sharia.screen("KO2", *company(debt=1e9, cash=1e9), price=200.0)
    assert v.verdict == "FAIL"


def test_sec_needs_an_identity(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(sharia.ScreenError, match="SEC_USER_AGENT"):
        sharia.cik_for("AAPL")
