from metrics import calculate_financial_metrics, passes_filters, safe_divide, valuation_score


def test_safe_divide_handles_zero_and_missing_values():
    assert safe_divide(10, 2) == 5
    assert safe_divide(10, 0) is None
    assert safe_divide(None, 2) is None


def test_calculate_financial_metrics():
    company = {
        "ticker": "TEST",
        "company_name": "Test Co",
        "price": 5,
        "shares": 10_000_000,
        "market_cap": 50_000_000,
        "current_assets": 100_000_000,
        "current_liabilities": 25_000_000,
        "debt": 10_000_000,
        "equity": 200_000_000,
        "enterprise_value": 60_000_000,
    }

    result = calculate_financial_metrics(company)

    assert result["net_current_assets"] == 75_000_000
    assert result["current_ratio"] == 4
    assert round(result["market_cap_to_nca"], 4) == 0.6667
    assert result["nca_per_share"] == 7.5
    assert round(result["debt_to_nca"], 4) == 0.1333
    assert result["debt_to_equity"] == 0.05
    assert result["graham_net_net"] is True


def test_passes_filters_requires_positive_nca_and_threshold():
    row = {
        "market_cap": 120_000_000,
        "net_current_assets": 100_000_000,
        "market_cap_to_nca": 1.2,
    }
    assert passes_filters(row, min_market_cap=100_000_000, max_market_cap_to_nca=2.0)

    row["market_cap_to_nca"] = 2.1
    assert not passes_filters(row, min_market_cap=100_000_000, max_market_cap_to_nca=2.0)

    row["market_cap_to_nca"] = 1.2
    row["net_current_assets"] = -1
    assert not passes_filters(row, min_market_cap=100_000_000, max_market_cap_to_nca=2.0)


def test_valuation_score_bounds():
    assert valuation_score(0.2, 0.3, 0.0, 5.0) == 100
    assert valuation_score(3.0, 4.0, 2.0, 0.5) == 0
