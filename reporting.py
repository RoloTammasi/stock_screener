from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pandas as pd

from config import ScreenerConfig
from screener import ScreenerResult


LOGGER = logging.getLogger(__name__)

CSV_COLUMNS = [
    "rank",
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
    "pe_ratio",
    "debt",
    "debt_to_nca",
    "debt_to_equity",
    "enterprise_value_to_nca",
    "net_net_value",
    "valuation_score",
    "graham_net_net",
    "passes_filters",
]


def write_reports(result: ScreenerResult, config: ScreenerConfig) -> dict[str, Path]:
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    all_csv = config.output_dir / "all_companies.csv"
    filtered_csv = config.output_dir / "filtered_companies.csv"
    timestamped_filtered_csv = config.output_dir / f"filtered_companies_{run_id}.csv"
    excel_path = config.output_dir / f"deep_value_screener_{run_id}.xlsx"

    _write_csv(result.all_companies, all_csv)
    _write_csv(result.filtered_companies, filtered_csv)
    _write_csv(result.filtered_companies, timestamped_filtered_csv)
    _write_excel(result, excel_path)
    store_history(result, config, run_id)

    return {
        "all_csv": all_csv,
        "filtered_csv": filtered_csv,
        "timestamped_filtered_csv": timestamped_filtered_csv,
        "excel": excel_path,
    }


def print_summary(result: ScreenerResult) -> None:
    all_count = len(result.all_companies)
    filtered_count = len(result.filtered_companies)
    graham_count = int(result.filtered_companies.get("graham_net_net", pd.Series(dtype=bool)).sum())
    print("")
    print("Deep Value Screener Summary")
    print("===========================")
    print(f"Companies processed: {all_count:,}")
    print(f"Passing filters:     {filtered_count:,}")
    print(f"Graham net-nets:     {graham_count:,}")
    print(f"Ticker errors:       {len(result.errors):,}")
    if filtered_count:
        top = result.filtered_companies.head(10)[
            ["rank", "ticker", "company_name", "market_cap_to_nca", "current_ratio", "valuation_score"]
        ]
        print("")
        print("Top candidates:")
        print(top.to_string(index=False))


def store_history(result: ScreenerResult, config: ScreenerConfig, run_id: str) -> None:
    with sqlite3.connect(config.database_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                run_at TEXT NOT NULL,
                all_count INTEGER NOT NULL,
                filtered_count INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                run_id TEXT NOT NULL,
                rank INTEGER,
                ticker TEXT NOT NULL,
                company_name TEXT,
                market_cap REAL,
                net_current_assets REAL,
                market_cap_to_nca REAL,
                current_ratio REAL,
                debt_to_nca REAL,
                valuation_score INTEGER,
                passes_filters INTEGER,
                PRIMARY KEY (run_id, ticker)
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?)",
            (
                run_id,
                datetime.now(UTC).isoformat(),
                len(result.all_companies),
                len(result.filtered_companies),
            ),
        )
        if not result.all_companies.empty:
            result.all_companies.assign(
                run_id=run_id,
                passes_filters=result.all_companies.get("passes_filters", False).astype(int),
            )[
                [
                    "run_id",
                    "rank",
                    "ticker",
                    "company_name",
                    "market_cap",
                    "net_current_assets",
                    "market_cap_to_nca",
                    "current_ratio",
                    "debt_to_nca",
                    "valuation_score",
                    "passes_filters",
                ]
            ].to_sql("results", conn, if_exists="append", index=False)


def latest_history_changes(config: ScreenerConfig) -> dict[str, list[str]]:
    if not config.database_path.exists():
        return {"entered": [], "left": []}
    with sqlite3.connect(config.database_path) as conn:
        runs = [row[0] for row in conn.execute("SELECT run_id FROM runs ORDER BY run_at DESC LIMIT 2")]
        if len(runs) < 2:
            return {"entered": [], "left": []}
        latest, previous = runs[0], runs[1]
        latest_tickers = _filtered_tickers(conn, latest)
        previous_tickers = _filtered_tickers(conn, previous)
        return {
            "entered": sorted(latest_tickers - previous_tickers),
            "left": sorted(previous_tickers - latest_tickers),
        }


def send_email_report(result: ScreenerResult, config: ScreenerConfig, artifacts: dict[str, Path]) -> None:
    if not config.email_enabled:
        return
    required = [config.smtp_host, config.smtp_from, config.smtp_to]
    if not all(required):
        LOGGER.warning("Email report enabled but SMTP_HOST, SMTP_FROM, or SMTP_TO is missing.")
        return

    message = EmailMessage()
    message["Subject"] = "Deep Value Screener Results"
    message["From"] = config.smtp_from
    message["To"] = config.smtp_to
    top = result.filtered_companies.head(10)[
        ["rank", "ticker", "market_cap_to_nca", "current_ratio", "valuation_score"]
    ]
    message.set_content(
        "Deep Value Screener run complete.\n\n"
        f"Companies processed: {len(result.all_companies):,}\n"
        f"Passing filters: {len(result.filtered_companies):,}\n\n"
        "Top candidates:\n"
        f"{top.to_string(index=False) if not top.empty else 'None'}\n\n"
        f"Excel report: {artifacts['excel']}\n"
    )

    with smtplib.SMTP(config.smtp_host, config.smtp_port) as smtp:
        if config.smtp_use_tls:
            smtp.starttls()
        if config.smtp_username:
            smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    columns = [column for column in CSV_COLUMNS if column in frame.columns]
    frame.to_csv(path, columns=columns, index=False)


def _write_excel(result: ScreenerResult, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        result.all_companies.to_excel(writer, sheet_name="All Companies", index=False)
        result.filtered_companies.to_excel(writer, sheet_name="Filtered Companies", index=False)
        result.top_50.to_excel(writer, sheet_name="Top 50 Ranked", index=False)
        _summary_frame(result).to_excel(writer, sheet_name="Summary", index=False)


def _summary_frame(result: ScreenerResult) -> pd.DataFrame:
    values: list[dict[str, Any]] = [
        {"metric": "Companies processed", "value": len(result.all_companies)},
        {"metric": "Passing filters", "value": len(result.filtered_companies)},
        {"metric": "Ticker errors", "value": len(result.errors)},
    ]
    if not result.filtered_companies.empty:
        values.extend(
            [
                {
                    "metric": "Median MC/NCA among filtered",
                    "value": result.filtered_companies["market_cap_to_nca"].median(),
                },
                {
                    "metric": "Median current ratio among filtered",
                    "value": result.filtered_companies["current_ratio"].median(),
                },
            ]
        )
    return pd.DataFrame(values)


def _filtered_tickers(conn: sqlite3.Connection, run_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT ticker FROM results WHERE run_id = ? AND passes_filters = 1",
        (run_id,),
    )
    return {row[0] for row in rows}
