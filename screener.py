from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

import pandas as pd
from tqdm import tqdm

from config import ScreenerConfig
from data_provider import DataProvider, FinancialModelingPrepProvider
from metrics import calculate_financial_metrics, passes_filters


LOGGER = logging.getLogger(__name__)

RANK_COLUMNS = ["market_cap_to_nca", "current_ratio", "debt_to_equity"]


@dataclass
class ScreenerResult:
    all_companies: pd.DataFrame
    filtered_companies: pd.DataFrame
    top_50: pd.DataFrame
    errors: list[dict[str, str]]
    cancelled: bool = False


ProgressCallback = Callable[[dict[str, Any]], None]


def run_screener(
    config: ScreenerConfig,
    provider: DataProvider | None = None,
    show_progress: bool = True,
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback | None = None,
) -> ScreenerResult:
    provider = provider or FinancialModelingPrepProvider(config)
    tickers = provider.get_universe()
    LOGGER.info("Screening %d tickers.", len(tickers))
    _emit_progress(
        progress_callback,
        event="universe",
        message=f"Loaded {len(tickers):,} tickers to screen.",
        processed=0,
        total=len(tickers),
        rows=0,
        errors=0,
    )

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    cancelled = False

    iterator = tqdm(tickers, desc="Screening", unit="ticker", disable=not show_progress)
    for processed, ticker in enumerate(iterator, start=1):
        if cancel_event and cancel_event.is_set():
            cancelled = True
            LOGGER.info("Screening cancelled after %d of %d tickers.", processed - 1, len(tickers))
            _emit_progress(
                progress_callback,
                event="cancelled",
                message=f"Stop requested. Cancelled after {processed - 1:,} of {len(tickers):,} tickers.",
                processed=processed - 1,
                total=len(tickers),
                rows=len(rows),
                errors=len(errors),
            )
            break
        try:
            company = provider.get_company_data(ticker)
            if not company:
                LOGGER.debug("Skipping %s because no company data was returned.", ticker)
            else:
                rows.append(calculate_financial_metrics(company))
        except Exception as exc:  # noqa: BLE001 - per-ticker isolation is intentional.
            LOGGER.exception("Failed to process %s", ticker)
            errors.append({"ticker": ticker, "error": str(exc)})
        finally:
            _emit_progress(
                progress_callback,
                event="progress",
                message=f"Screened {processed:,} of {len(tickers):,} tickers.",
                processed=processed,
                total=len(tickers),
                ticker=ticker,
                rows=len(rows),
                errors=len(errors),
            )

    all_companies = _prepare_dataframe(rows)
    if all_companies.empty:
        all_companies.insert(0, "rank", pd.Series(dtype="int64"))
        all_companies["passes_filters"] = pd.Series(dtype="bool")
        return ScreenerResult(
            all_companies,
            all_companies.copy(),
            all_companies.copy(),
            errors,
            cancelled,
        )

    all_companies["passes_filters"] = all_companies.apply(
        lambda row: passes_filters(
            row.to_dict(),
            min_market_cap=config.min_market_cap,
            max_market_cap_to_nca=config.max_market_cap_to_nca,
        ),
        axis=1,
    )
    ranked = rank_companies(all_companies)
    filtered = ranked[ranked["passes_filters"]].copy()
    top_50 = filtered.head(50).copy()
    return ScreenerResult(ranked, filtered, top_50, errors, cancelled)


def _emit_progress(callback: ProgressCallback | None, **payload: Any) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:  # noqa: BLE001 - progress reporting should not stop a run.
        LOGGER.exception("Progress callback failed.")


def rank_companies(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    ranked = frame.copy()
    ranked["_mc_nca_sort"] = ranked["market_cap_to_nca"].where(
        (ranked["net_current_assets"] > 0) & (ranked["market_cap_to_nca"] > 0),
        float("inf"),
    )
    ranked["_current_ratio_sort"] = ranked["current_ratio"].fillna(float("-inf"))
    ranked["_debt_to_equity_sort"] = ranked["debt_to_equity"].fillna(float("inf"))
    ranked = ranked.sort_values(
        by=["_mc_nca_sort", "_current_ratio_sort", "_debt_to_equity_sort", "ticker"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop(columns=["_mc_nca_sort", "_current_ratio_sort", "_debt_to_equity_sort"])
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    return ranked


def _prepare_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in OUTPUT_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[OUTPUT_COLUMNS]


OUTPUT_COLUMNS = [
    "ticker",
    "company_name",
    "price",
    "shares",
    "market_cap",
    "current_assets",
    "current_liabilities",
    "net_current_assets",
    "current_ratio",
    "market_cap_to_nca",
    "nca_to_market_cap",
    "nca_per_share",
    "debt",
    "debt_to_nca",
    "debt_to_equity",
    "enterprise_value",
    "enterprise_value_to_nca",
    "net_net_value",
    "graham_net_net",
    "valuation_score",
    "exchange",
    "country",
    "source",
]
