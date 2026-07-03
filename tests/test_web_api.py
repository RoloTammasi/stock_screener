import json
from threading import Event

import pandas as pd

from main import WebRunState, empty_cache, prepare_json_frame, prepare_watchlist_export_frame


def test_prepare_json_frame_outputs_strict_json_values():
    frame = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "current_ratio": float("nan"),
                "enterprise_value_to_nca": float("inf"),
            },
            {
                "ticker": "BBB",
                "current_ratio": 2.5,
                "enterprise_value_to_nca": -float("inf"),
            },
        ]
    )

    records = prepare_json_frame(frame).to_dict(orient="records")
    encoded = json.dumps(records, allow_nan=False)

    assert json.loads(encoded) == [
        {"ticker": "AAA", "current_ratio": None, "enterprise_value_to_nca": None},
        {"ticker": "BBB", "current_ratio": 2.5, "enterprise_value_to_nca": None},
    ]


def test_web_run_state_exposes_cancel_request():
    state = WebRunState()
    release = Event()

    def target():
        release.wait(timeout=2)
        state.finish("completed", "done")

    assert state.start(target) is True
    state.request_cancel()

    snapshot = state.snapshot()
    assert snapshot["running"] is True
    assert snapshot["cancel_requested"] is True
    assert "Stop requested" in snapshot["message"]

    release.set()
    state.thread.join(timeout=2)


def test_empty_cache_removes_nested_cached_files(tmp_path):
    cache_dir = tmp_path / "cache"
    nested = cache_dir / "http" / "nested"
    nested.mkdir(parents=True)
    (cache_dir / "root.json").write_text("{}", encoding="utf-8")
    (nested / "response.json").write_text("{}", encoding="utf-8")

    removed = empty_cache(cache_dir)

    assert removed == 2
    assert cache_dir.exists()
    assert list(cache_dir.iterdir()) == []


def test_prepare_watchlist_export_frame_preserves_visible_columns_and_order():
    frame = pd.DataFrame(
        [
            {
                "ticker": "BBB",
                "company_name": "Beta Co",
                "price": 20,
                "shares": 2_000_000,
                "market_cap": 40_000_000,
                "current_assets": 50_000_000,
                "current_liabilities": 20_000_000,
                "net_current_assets": 30_000_000,
                "current_ratio": 2.5,
                "market_cap_to_nca": 1.33,
                "nca_per_share": 15,
                "pe_ratio": 11.2,
                "debt": 5_000_000,
                "debt_to_nca": 0.17,
            },
            {
                "ticker": "AAA",
                "company_name": "Alpha Co",
                "price": 10,
                "shares": 1_000_000,
                "market_cap": 10_000_000,
                "current_assets": 30_000_000,
                "current_liabilities": 5_000_000,
                "net_current_assets": 25_000_000,
                "current_ratio": 6,
                "market_cap_to_nca": 0.4,
                "nca_per_share": 25,
                "pe_ratio": 7.4,
                "debt": 1_000_000,
                "debt_to_nca": 0.04,
            },
        ]
    )

    export = prepare_watchlist_export_frame(frame, ["AAA", "BBB"])

    assert list(export["Symbol"]) == ["AAA", "BBB"]
    assert list(export.columns) == [
        "Symbol",
        "Name",
        "Price",
        "Shares",
        "Market Cap (MC)",
        "Current Assets",
        "Current Liabilities",
        "Net Current Assets (NCA)",
        "Current Ratio",
        "MC/NCA",
        "NCA/Share",
        "Margin of Safety",
        "PE Ratio",
        "Debt",
        "Debt/NCA",
    ]
    assert round(export.iloc[0]["Margin of Safety"], 2) == 16.67
    assert export.iloc[0]["PE Ratio"] == 7.4
