from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Screening thresholds. These can be overridden in .env.
MIN_MARKET_CAP = 100_000_000
MAX_MARKET_CAP_TO_NCA = 2.0


PROJECT_ROOT = Path(__file__).resolve().parent


def _bool_from_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


@dataclass(frozen=True)
class ScreenerConfig:
    fmp_api_key: str
    sec_user_agent: str
    min_market_cap: int = MIN_MARKET_CAP
    max_market_cap_to_nca: float = MAX_MARKET_CAP_TO_NCA
    max_tickers: int | None = None
    cache_ttl_hours: int = 720
    output_dir: Path = PROJECT_ROOT / "output"
    cache_dir: Path = PROJECT_ROOT / "cache"
    log_dir: Path = PROJECT_ROOT / "logs"
    data_dir: Path = PROJECT_ROOT / "data"
    database_path: Path = PROJECT_ROOT / "data" / "screener_history.sqlite3"
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_use_tls: bool = True

    def ensure_directories(self) -> None:
        for path in (self.output_dir, self.cache_dir, self.log_dir, self.data_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_config() -> ScreenerConfig:
    load_dotenv(PROJECT_ROOT / ".env")

    config = ScreenerConfig(
        fmp_api_key=os.getenv("FMP_API_KEY", "").strip(),
        sec_user_agent=os.getenv(
            "SEC_USER_AGENT", "DeepValueScreener/1.0 contact@example.com"
        ).strip(),
        min_market_cap=int(os.getenv("MIN_MARKET_CAP", str(MIN_MARKET_CAP))),
        max_market_cap_to_nca=float(
            os.getenv("MAX_MARKET_CAP_TO_NCA", str(MAX_MARKET_CAP_TO_NCA))
        ),
        max_tickers=_optional_int("MAX_TICKERS"),
        cache_ttl_hours=int(os.getenv("CACHE_TTL_HOURS", "720")),
        output_dir=Path(os.getenv("OUTPUT_DIR", PROJECT_ROOT / "output")).expanduser(),
        cache_dir=Path(os.getenv("CACHE_DIR", PROJECT_ROOT / "cache")).expanduser(),
        log_dir=Path(os.getenv("LOG_DIR", PROJECT_ROOT / "logs")).expanduser(),
        data_dir=Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")).expanduser(),
        email_enabled=_bool_from_env("EMAIL_ENABLED", False),
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
        smtp_from=os.getenv("SMTP_FROM", "").strip(),
        smtp_to=os.getenv("SMTP_TO", "").strip(),
        smtp_use_tls=_bool_from_env("SMTP_USE_TLS", True),
    )
    object.__setattr__(config, "database_path", config.data_dir / "screener_history.sqlite3")
    config.ensure_directories()
    return config
