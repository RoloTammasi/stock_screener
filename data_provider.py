from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from requests import HTTPError

from config import ScreenerConfig


LOGGER = logging.getLogger(__name__)

US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSEAMERICAN", "NYSE AMERICAN"}
EXCLUDED_NAME_PATTERNS = (
    " ADR",
    " ADS",
    "%",
    " BOND",
    " DEBENTURE",
    " WARRANT",
    " RIGHT",
    " UNIT",
    " NOTE ",
    " NOTES",
    " PREFERRED",
    " PREFERENCE",
    " PFD",
    " DEPOSITARY",
    " SENIOR ",
    " SUBORDINATED",
    " FIXED",
    " FLOATING",
    " DUE ",
    " BD ",
    " ACQUISITION CORP",
    " ACQUISITION COMPANY",
    " BLANK CHECK",
    " SPAC",
)
EXCLUDED_SYMBOL_PATTERNS = (
    re.compile(r".*[-.]?W(S|T)?$", re.IGNORECASE),
    re.compile(r".*[-.]?U$", re.IGNORECASE),
    re.compile(r".*[-.]?R$", re.IGNORECASE),
    re.compile(r".*[-.]?P(R[A-Z]?)?$", re.IGNORECASE),
)


@dataclass
class HttpCache:
    directory: Path
    ttl_seconds: int

    def get(self, url: str, params: dict[str, Any] | None) -> Any | None:
        path = self._path(url, params)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Ignoring unreadable cache file %s", path)
            return None

    def set(self, url: str, params: dict[str, Any] | None, payload: Any) -> None:
        path = self._path(url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        tmp_path.replace(path)

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        cache_key = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"


class DataProvider(ABC):
    @abstractmethod
    def get_universe(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_company_data(self, ticker: str) -> dict[str, Any] | None:
        raise NotImplementedError


class CachedSession:
    def __init__(self, cache: HttpCache, timeout: int = 30, min_interval_seconds: float = 0.25):
        self.cache = cache
        self.timeout = timeout
        self.min_interval_seconds = min_interval_seconds
        self.session = requests.Session()
        self._last_request_at = 0.0

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        max_retries: int = 5,
    ) -> Any:
        if use_cache:
            cached = self.cache.get(url, params)
            if cached is not None:
                return cached

        for attempt in range(max_retries):
            self._respect_rate_limit()
            response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            if response.status_code == 429 or response.status_code >= 500:
                delay = self._retry_delay(response, attempt)
                LOGGER.warning(
                    "Retryable response %s for %s. Sleeping %.1fs.",
                    response.status_code,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue
            response.raise_for_status()
            payload = response.json()
            if use_cache:
                self.cache.set(url, params, payload)
            return payload

        response.raise_for_status()
        return response.json()

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_at = time.time()

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return min(60.0, 2**attempt + attempt * 0.5)


class FinancialModelingPrepProvider(DataProvider):
    base_url = "https://financialmodelingprep.com/stable"

    def __init__(self, config: ScreenerConfig):
        if not config.fmp_api_key:
            raise ValueError("FMP_API_KEY is required. Add it to .env.")
        self.config = config
        self.cache = HttpCache(config.cache_dir / "http", config.cache_ttl_hours * 3600)
        self.client = CachedSession(self.cache)
        self.sec = SecEdgarFallbackProvider(config, self.client)
        self.universe_records: dict[str, dict[str, Any]] = {}

    def get_universe(self) -> list[str]:
        try:
            records = self._get_fmp(
                "company-screener",
                {
                    "marketCapMoreThan": self.config.min_market_cap,
                    "isEtf": "false",
                    "isFund": "false",
                    "isActivelyTrading": "true",
                    "limit": 10000,
                },
            )
        except HTTPError as exc:
            LOGGER.warning(
                "FMP company-screener endpoint failed with %s; falling back to stock-list.",
                exc.response.status_code if exc.response is not None else "unknown status",
            )
            records = []
        if not isinstance(records, list) or not records:
            LOGGER.warning("FMP company-screener returned no records; falling back to stock-list.")
            records = self._get_fmp("stock-list", {})

        tickers: list[str] = []
        seen: set[str] = set()
        for record in records:
            if self._eligible_universe_record(record):
                symbol = str(record.get("symbol", "")).strip().upper()
                if symbol and symbol not in seen:
                    tickers.append(symbol)
                    seen.add(symbol)
                    self.universe_records[symbol] = record

        if self.config.max_tickers:
            tickers = tickers[: self.config.max_tickers]
        return tickers

    def get_company_data(self, ticker: str) -> dict[str, Any] | None:
        profile = self.universe_records.get(ticker.upper()) or {}
        if not profile:
            profile = self._first(self._get_fmp("profile", {"symbol": ticker})) or {}
        if not profile or not self._eligible_profile(profile):
            return None

        balance = self._first(
            self._get_fmp(
                "balance-sheet-statement",
                {"symbol": ticker, "period": "annual", "limit": 1},
            )
        )
        enterprise = self._first(
            self._get_fmp("enterprise-values", {"symbol": ticker, "limit": 1})
        ) or {}

        if not balance:
            if "cik" not in profile:
                profile = self._first(self._get_fmp("profile", {"symbol": ticker})) or profile
            balance = self.sec.get_latest_balance_sheet(profile.get("cik"), ticker)
        else:
            balance = self._normalize_fmp_balance(balance)

        if not balance:
            LOGGER.info("Skipping %s because balance sheet data was unavailable.", ticker)
            return None

        price = _first_number(profile.get("price"), enterprise.get("stockPrice"))
        market_cap = _first_number(profile.get("marketCap"), profile.get("mktCap"))
        shares = _first_number(
            enterprise.get("numberOfShares"),
            balance.get("shares"),
            _safe_divide(market_cap, price),
        )
        enterprise_value = _first_number(enterprise.get("enterpriseValue"))

        return {
            "ticker": ticker.upper(),
            "company_name": profile.get("companyName") or profile.get("company_name") or ticker,
            "price": price,
            "shares": shares,
            "market_cap": market_cap,
            "current_assets": balance.get("current_assets"),
            "current_liabilities": balance.get("current_liabilities"),
            "debt": balance.get("debt"),
            "equity": balance.get("equity"),
            "enterprise_value": enterprise_value,
            "exchange": profile.get("exchangeShortName") or profile.get("exchange"),
            "country": profile.get("country"),
            "source": "fmp" if balance.get("source") != "sec" else "sec",
        }

    def _get_fmp(self, endpoint: str, params: dict[str, Any]) -> Any:
        all_params = {**params, "apikey": self.config.fmp_api_key}
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        return self.client.get_json(url, all_params)

    @staticmethod
    def _first(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, list) and payload:
            return payload[0]
        if isinstance(payload, dict):
            return payload
        return None

    @staticmethod
    def _normalize_fmp_balance(balance: dict[str, Any]) -> dict[str, Any]:
        return {
            "current_assets": balance.get("totalCurrentAssets"),
            "current_liabilities": balance.get("totalCurrentLiabilities"),
            "debt": _sum_numbers(balance.get("shortTermDebt"), balance.get("longTermDebt"))
            or balance.get("totalDebt"),
            "equity": balance.get("totalStockholdersEquity"),
            "shares": balance.get("commonStockSharesOutstanding"),
            "source": "fmp",
        }

    def _eligible_universe_record(self, record: dict[str, Any]) -> bool:
        symbol = str(record.get("symbol", "")).strip().upper()
        name = str(record.get("companyName") or record.get("name") or "").upper()
        exchange = str(record.get("exchangeShortName") or record.get("exchange") or "").upper()
        market_cap = _first_number(record.get("marketCap"))
        if not symbol:
            return False
        if _truthy_flag(record.get("isEtf")) or _truthy_flag(record.get("isFund")):
            return False
        if not _truthy_flag(record.get("isActivelyTrading"), default=True):
            return False
        if market_cap is not None and market_cap < self.config.min_market_cap:
            return False
        if exchange.replace(" ", "") not in {item.replace(" ", "") for item in US_EXCHANGES}:
            return False
        return not _looks_excluded(symbol, name)

    def _eligible_profile(self, profile: dict[str, Any]) -> bool:
        symbol = str(profile.get("symbol", "")).strip().upper()
        name = str(profile.get("companyName") or "").upper()
        exchange = str(profile.get("exchangeShortName") or profile.get("exchange") or "").upper()
        country = str(profile.get("country") or "").upper()
        if exchange.replace(" ", "") not in {item.replace(" ", "") for item in US_EXCHANGES}:
            return False
        if country and country != "US":
            return False
        if (
            _truthy_flag(profile.get("isEtf"))
            or _truthy_flag(profile.get("isFund"))
            or _truthy_flag(profile.get("isAdr"))
        ):
            return False
        return not _looks_excluded(symbol, name)


class SecEdgarFallbackProvider:
    base_url = "https://data.sec.gov/api/xbrl/companyfacts"

    def __init__(self, config: ScreenerConfig, client: CachedSession):
        self.config = config
        self.client = client

    def get_latest_balance_sheet(self, cik: str | int | None, ticker: str) -> dict[str, Any] | None:
        if not cik:
            return None
        cik_str = str(cik).strip().lstrip("0").zfill(10)
        url = f"{self.base_url}/CIK{cik_str}.json"
        headers = {"User-Agent": self.config.sec_user_agent}
        try:
            payload = self.client.get_json(url, headers=headers)
        except requests.RequestException as exc:
            LOGGER.warning("SEC fallback failed for %s: %s", ticker, exc)
            return None

        facts = payload.get("facts", {}).get("us-gaap", {}) if isinstance(payload, dict) else {}
        current_assets = _latest_usd_fact(facts.get("AssetsCurrent"))
        current_liabilities = _latest_usd_fact(facts.get("LiabilitiesCurrent"))
        debt = _sum_numbers(
            _latest_usd_fact(facts.get("ShortTermBorrowings")),
            _latest_usd_fact(facts.get("LongTermDebt")),
            _latest_usd_fact(facts.get("LongTermDebtCurrent")),
        )
        equity = _latest_usd_fact(facts.get("StockholdersEquity"))

        if current_assets is None or current_liabilities is None:
            return None
        return {
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "debt": debt or 0,
            "equity": equity,
            "source": "sec",
        }


def _latest_usd_fact(tag: dict[str, Any] | None) -> float | None:
    if not tag:
        return None
    units = tag.get("units", {})
    facts = units.get("USD") or units.get("shares") or []
    valid = [
        item
        for item in facts
        if item.get("val") is not None and item.get("form") in {"10-K", "10-Q", "20-F", "40-F"}
    ]
    if not valid:
        return None
    valid.sort(key=lambda item: (item.get("end", ""), item.get("filed", "")), reverse=True)
    return _first_number(valid[0].get("val"))


def _looks_excluded(symbol: str, name: str) -> bool:
    if any(pattern.search(symbol) for pattern in EXCLUDED_SYMBOL_PATTERNS):
        return True
    return any(pattern in f" {name} " for pattern in EXCLUDED_NAME_PATTERNS)


def _truthy_flag(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _sum_numbers(*values: Any) -> float | None:
    numbers = [_first_number(value) for value in values]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return None
    return sum(numbers)


def _safe_divide(left: float | None, right: float | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return left / right
