from __future__ import annotations

import argparse
import logging
import os
import signal
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, url_for

from config import load_config
from reporting import latest_history_changes, print_summary, send_email_report, write_reports
from screener import run_screener


LOGGER = logging.getLogger(__name__)


class WebRunState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "idle"
        self.running = False
        self.processed = 0
        self.total = 0
        self.rows = 0
        self.errors = 0
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.message = "No run active."
        self.error: str | None = None
        self.artifacts: dict[str, str] = {}
        self.logs: list[str] = []

    def start(self, target: Any) -> bool:
        with self.lock:
            if self.running:
                return False
            self.cancel_event.clear()
            self.status = "running"
            self.running = True
            self.processed = 0
            self.total = 0
            self.rows = 0
            self.errors = 0
            self.started_at = _utc_now()
            self.finished_at = None
            self.message = "Starting screener run..."
            self.error = None
            self.artifacts = {}
            self.logs = []
            self._append_log_locked("Starting screener run...")
            self.thread = threading.Thread(target=target, name="web-screener-run", daemon=True)
            self.thread.start()
            return True

    def request_cancel(self) -> None:
        with self.lock:
            if not self.running:
                self._append_log_locked("No active run to stop.")
                return
            self.cancel_event.set()
            self.message = "Stop requested. Waiting for the current API call to finish..."
            self._append_log_locked(self.message)

    def progress(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.processed = int(payload.get("processed") or self.processed or 0)
            self.total = int(payload.get("total") or self.total or 0)
            self.rows = int(payload.get("rows") or self.rows or 0)
            self.errors = int(payload.get("errors") or self.errors or 0)
            message = str(payload.get("message") or "").strip()
            if message:
                self.message = message
            event = payload.get("event")
            if event in {"universe", "reports", "cancelled"}:
                self._append_log_locked(self.message)
            elif event == "progress" and (
                self.processed == 1
                or self.processed == self.total
                or self.processed % 25 == 0
            ):
                ticker = payload.get("ticker")
                detail = f"{self.message} Rows kept: {self.rows:,}. Errors: {self.errors:,}."
                if ticker:
                    detail += f" Last ticker: {ticker}."
                self._append_log_locked(detail)

    def finish(self, status: str, message: str, artifacts: dict[str, Path] | None = None) -> None:
        with self.lock:
            self.status = status
            self.running = False
            self.finished_at = _utc_now()
            self.message = message
            self.artifacts = {name: str(path) for name, path in (artifacts or {}).items()}
            self._append_log_locked(message)

    def fail(self, exc: Exception) -> None:
        with self.lock:
            self.status = "failed"
            self.running = False
            self.finished_at = _utc_now()
            self.error = str(exc)
            self.message = f"Run failed: {exc}"
            self._append_log_locked(self.message)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": self.status,
                "running": self.running,
                "cancel_requested": self.cancel_event.is_set(),
                "processed": self.processed,
                "total": self.total,
                "rows": self.rows,
                "errors": self.errors,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "message": self.message,
                "error": self.error,
                "artifacts": self.artifacts,
                "logs": list(self.logs),
            }

    def _append_log_locked(self, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        self.logs = self.logs[-250:]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "screener.log", encoding="utf-8"),
        ],
    )


def run_once(limit: int | None = None, no_email: bool = False) -> dict[str, Path]:
    config = load_config()
    if limit is not None:
        object.__setattr__(config, "max_tickers", limit)
    if no_email:
        object.__setattr__(config, "email_enabled", False)
    configure_logging(config.log_dir)
    result = run_screener(config)
    artifacts = write_reports(result, config)
    print_summary(result)
    send_email_report(result, config, artifacts)
    print("")
    print("Artifacts:")
    for name, path in artifacts.items():
        print(f"- {name}: {path}")
    return artifacts


def create_app() -> Flask:
    config = load_config()
    configure_logging(config.log_dir)
    app = Flask(__name__)
    run_state = WebRunState()

    @app.get("/")
    def index():
        changes = latest_history_changes(config)
        return render_template("index.html", changes=changes)

    @app.post("/run")
    def run_from_web():
        _start_web_run(config, run_state)
        return redirect(url_for("index"))

    @app.post("/api/run")
    def api_run():
        started = _start_web_run(config, run_state)
        status_code = 202 if started else 409
        return jsonify(run_state.snapshot()), status_code

    @app.post("/api/stop-run")
    def api_stop_run():
        run_state.request_cancel()
        return jsonify(run_state.snapshot())

    @app.get("/api/run-status")
    def api_run_status():
        return jsonify(run_state.snapshot())

    @app.post("/api/cache/clear")
    def api_clear_cache():
        if run_state.snapshot()["running"]:
            return (
                jsonify({"message": "Stop the current screener run before emptying the cache."}),
                409,
            )
        removed = empty_cache(config.cache_dir)
        message = f"Cache emptied. Removed {removed:,} cached file(s)."
        LOGGER.info(message)
        return jsonify({"message": message, "removed": removed})

    @app.post("/shutdown")
    def shutdown():
        LOGGER.info("Shutdown requested from web UI.")
        threading.Timer(0.2, _stop_process).start()
        return "Deep Value Screener is shutting down. You can close this browser tab."

    @app.get("/api/results")
    def api_results():
        kind = request.args.get("kind", "filtered")
        path = config.output_dir / ("all_companies.csv" if kind == "all" else "filtered_companies.csv")
        if not path.exists():
            return jsonify({"rows": [], "columns": [], "message": "No results yet. Run the screener first."})
        frame = pd.read_csv(path)
        frame = prepare_json_frame(frame)
        return jsonify({"rows": frame.to_dict(orient="records"), "columns": list(frame.columns)})

    return app


def _start_web_run(config: Any, run_state: WebRunState) -> bool:
    return run_state.start(lambda: _run_web_job(config, run_state))


def _run_web_job(config: Any, run_state: WebRunState) -> None:
    try:
        result = run_screener(
            config,
            show_progress=False,
            cancel_event=run_state.cancel_event,
            progress_callback=run_state.progress,
        )
        if result.cancelled or run_state.cancel_event.is_set():
            run_state.finish(
                "cancelled",
                "Run stopped. Previous report files were left unchanged.",
            )
            return
        run_state.progress(
            {
                "event": "reports",
                "message": "Writing CSV, Excel, and history reports...",
                "processed": run_state.processed,
                "total": run_state.total,
                "rows": len(result.all_companies),
                "errors": len(result.errors),
            }
        )
        artifacts = write_reports(result, config)
        print_summary(result)
        run_state.finish(
            "completed",
            (
                f"Run complete. Processed {len(result.all_companies):,} companies; "
                f"{len(result.filtered_companies):,} passed filters."
            ),
            artifacts,
        )
    except Exception as exc:  # noqa: BLE001 - keep the web server alive after run failures.
        LOGGER.exception("Web screener run failed.")
        run_state.fail(exc)


def _stop_process() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def empty_cache(cache_dir: Path) -> int:
    if not cache_dir.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        return 0

    removed_files = sum(1 for path in cache_dir.rglob("*") if path.is_file())
    for child in cache_dir.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return removed_files


def prepare_json_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.replace([float("inf"), float("-inf")], pd.NA).astype(object)
    return cleaned.where(pd.notna(cleaned), None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deep value stock screener")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the screener once and write reports")
    run_parser.add_argument("--limit", type=int, default=None, help="Limit tickers for testing")
    run_parser.add_argument("--no-email", action="store_true", help="Disable optional email report")

    serve_parser = subparsers.add_parser("serve", help="Start the local web UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=5050)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "run":
        run_once(limit=args.limit, no_email=args.no_email)
        return

    app = create_app()
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 5050)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
