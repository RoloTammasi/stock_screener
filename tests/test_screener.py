from threading import Event

from config import ScreenerConfig
from screener import run_screener


class FakeProvider:
    def get_universe(self):
        return ["AAA", "BBB", "ERR"]

    def get_company_data(self, ticker):
        if ticker == "ERR":
            raise RuntimeError("bad ticker")
        if ticker == "AAA":
            return {
                "ticker": "AAA",
                "company_name": "AAA Corp",
                "price": 10,
                "shares": 10_000_000,
                "market_cap": 100_000_000,
                "current_assets": 200_000_000,
                "current_liabilities": 50_000_000,
                "debt": 5_000_000,
                "equity": 100_000_000,
                "pe_ratio": 8.5,
            }
        return {
            "ticker": "BBB",
            "company_name": "BBB Corp",
            "price": 20,
            "shares": 10_000_000,
            "market_cap": 200_000_000,
            "current_assets": 120_000_000,
            "current_liabilities": 80_000_000,
            "debt": 90_000_000,
            "equity": 50_000_000,
        }


def test_run_screener_filters_ranks_and_continues_on_errors(tmp_path):
    config = ScreenerConfig(
        fmp_api_key="test",
        sec_user_agent="test@example.com",
        min_market_cap=50_000_000,
        max_market_cap_to_nca=2.0,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )
    config.ensure_directories()

    result = run_screener(config, provider=FakeProvider(), show_progress=False)

    assert len(result.all_companies) == 2
    assert len(result.filtered_companies) == 1
    assert result.filtered_companies.iloc[0]["ticker"] == "AAA"
    assert result.filtered_companies.iloc[0]["pe_ratio"] == 8.5
    assert result.all_companies.iloc[0]["ticker"] == "AAA"
    assert result.errors == [{"ticker": "ERR", "error": "bad ticker"}]


def test_run_screener_can_be_cancelled_before_processing(tmp_path):
    config = ScreenerConfig(
        fmp_api_key="test",
        sec_user_agent="test@example.com",
        min_market_cap=50_000_000,
        max_market_cap_to_nca=2.0,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )
    config.ensure_directories()
    cancel_event = Event()
    cancel_event.set()

    result = run_screener(
        config,
        provider=FakeProvider(),
        show_progress=False,
        cancel_event=cancel_event,
    )

    assert result.cancelled is True
    assert result.all_companies.empty
    assert result.filtered_companies.empty
    assert result.errors == []
