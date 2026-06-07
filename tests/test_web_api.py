import json
from threading import Event

import pandas as pd

from main import WebRunState, empty_cache, prepare_json_frame


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
