from __future__ import annotations

from math import isfinite
from typing import Any


def safe_divide(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    result = float(numerator) / float(denominator)
    return result if isfinite(result) else None


def calculate_financial_metrics(company: dict[str, Any]) -> dict[str, Any]:
    current_assets = _num(company.get("current_assets"))
    current_liabilities = _num(company.get("current_liabilities"))
    market_cap = _num(company.get("market_cap"))
    shares = _num(company.get("shares"))
    debt = _num(company.get("debt")) or 0.0
    equity = _num(company.get("equity"))
    enterprise_value = _num(company.get("enterprise_value"))

    nca = None
    if not _has_missing(current_assets, current_liabilities):
        nca = current_assets - current_liabilities

    net_net_value = None if nca is None else nca - debt
    mc_to_nca = safe_divide(market_cap, nca)
    nca_to_mc = safe_divide(nca, market_cap)
    nca_per_share = safe_divide(nca, shares)
    current_ratio = safe_divide(current_assets, current_liabilities)
    debt_to_nca = safe_divide(debt, nca)
    debt_to_equity = safe_divide(debt, equity)
    ev_to_nca = safe_divide(enterprise_value, nca)

    result = {
        **company,
        "net_current_assets": nca,
        "current_ratio": current_ratio,
        "market_cap_to_nca": mc_to_nca,
        "nca_to_market_cap": nca_to_mc,
        "nca_per_share": nca_per_share,
        "debt_to_nca": debt_to_nca,
        "debt_to_equity": debt_to_equity,
        "enterprise_value_to_nca": ev_to_nca,
        "net_net_value": net_net_value,
        "graham_net_net": _less_than(market_cap, nca) and _less_than(market_cap, net_net_value),
        "valuation_score": valuation_score(mc_to_nca, ev_to_nca, debt_to_nca, current_ratio),
    }
    return result


def passes_filters(
    company: dict[str, Any],
    min_market_cap: float,
    max_market_cap_to_nca: float,
) -> bool:
    market_cap = _num(company.get("market_cap"))
    nca = _num(company.get("net_current_assets"))
    mc_to_nca = _num(company.get("market_cap_to_nca"))
    if market_cap is None or market_cap < min_market_cap:
        return False
    if nca is None or nca <= 0:
        return False
    if mc_to_nca is None:
        return False
    return mc_to_nca < max_market_cap_to_nca


def valuation_score(
    market_cap_to_nca: float | None,
    enterprise_value_to_nca: float | None,
    debt_to_nca: float | None,
    current_ratio: float | None,
) -> int:
    score = 0.0

    if market_cap_to_nca is not None:
        score += _scaled_inverse(market_cap_to_nca, excellent=0.5, poor=2.0) * 45
    if enterprise_value_to_nca is not None:
        score += _scaled_inverse(enterprise_value_to_nca, excellent=0.5, poor=2.5) * 25
    if debt_to_nca is not None:
        score += _scaled_inverse(debt_to_nca, excellent=0.0, poor=1.0) * 20
    if current_ratio is not None:
        score += _scaled_direct(current_ratio, poor=1.0, excellent=3.0) * 10

    return max(0, min(100, round(score)))


def _scaled_inverse(value: float, excellent: float, poor: float) -> float:
    if value <= excellent:
        return 1.0
    if value >= poor:
        return 0.0
    return (poor - value) / (poor - excellent)


def _scaled_direct(value: float, poor: float, excellent: float) -> float:
    if value >= excellent:
        return 1.0
    if value <= poor:
        return 0.0
    return (value - poor) / (excellent - poor)


def _less_than(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and left < right


def _has_missing(*values: float | None) -> bool:
    return any(value is None for value in values)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None
