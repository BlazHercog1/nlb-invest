"""Resolve holdings and fetch stock and foreign exchange prices from Yahoo."""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .analytics import return_between
from .currency import convert_to_eur
from .http_client import HttpClient
from .models import Holding, MarketPoint


# Exact ISIN overrides favor liquid primary listings over thin cross-listings.
SYMBOL_OVERRIDES = {
    "KYG3902L1095": "9698.HK",
    "US02079K3059": "GOOGL",
    "US02319V1035": "ABEV",
    "US4655621062": "ITUB",
    "US7960508882": "SMSN.IL",
    "US8740391003": "TSM",
}


class YahooClient:
    SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, cache_path: Path, refresh: bool = False) -> None:
        self.http = HttpClient()
        self.cache_path = cache_path
        self.refresh = refresh
        self.cache = self._load_cache()
        self.cache_lock = threading.Lock()
        self.fx_cache: dict[tuple[str, date, date, bool], tuple[list[MarketPoint], str | None]] = {}
        self.fx_lock = threading.Lock()

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if not self.cache_path.exists():
            return {}
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_cache(self) -> None:
        self.cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def resolve_isin(self, holding: Holding) -> str | None:
        if holding.isin in SYMBOL_OVERRIDES:
            symbol = SYMBOL_OVERRIDES[holding.isin]
            with self.cache_lock:
                self.cache[holding.isin] = {
                    "symbol": symbol,
                    "issuer": holding.issuer,
                    "source": "built-in override",
                }
            return symbol
        if not self.refresh and holding.isin in self.cache and self.cache[holding.isin].get("symbol"):
            return self.cache[holding.isin].get("symbol")
        try:
            allowed: list[dict[str, Any]] = []
            for query in (holding.isin, holding.issuer):
                params = urllib.parse.urlencode(
                    {"q": query, "quotesCount": 8, "newsCount": 0, "enableFuzzyQuery": "false"}
                )
                data = self.http.get_json(f"{self.SEARCH_URL}?{params}")
                quotes = data.get("quotes", [])
                allowed = [q for q in quotes if q.get("quoteType") in {"EQUITY", "ETF", "MUTUALFUND"}]
                if allowed:
                    break
            symbol = self._choose_symbol(allowed, holding)
            record = {
                "symbol": symbol,
                "issuer": holding.issuer,
                "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        except Exception as exc:  # Resolution failures are recorded and shown in coverage.
            symbol = None
            record = {"symbol": None, "issuer": holding.issuer, "error": str(exc)}
        with self.cache_lock:
            self.cache[holding.isin] = record
        return symbol

    @staticmethod
    def _choose_symbol(quotes: list[dict[str, Any]], holding: Holding) -> str | None:
        if not quotes:
            return None
        issuer_words = {word for word in re.findall(r"[A-Z0-9]+", holding.issuer.upper()) if len(word) >= 4}

        def score(quote: dict[str, Any]) -> tuple[int, int, int]:
            name = f"{quote.get('shortname', '')} {quote.get('longname', '')}".upper()
            overlap = sum(word in name for word in issuer_words)
            exchange = str(quote.get("exchange", ""))
            non_otc = exchange not in {"PNK", "OOTC", "GREY"}
            primary = bool(quote.get("isYahooFinance", True))
            return overlap, int(non_otc), int(primary)

        return max(quotes, key=score).get("symbol")

    def history(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        include_live: bool = False,
    ) -> tuple[str, list[MarketPoint], str | None]:
        # A wider lead-in avoids occasional one-point responses from Yahoo on
        # smaller exchanges while select_window still uses the requested dates.
        period1 = int(datetime.combine(start - timedelta(days=45), datetime.min.time(), tzinfo=timezone.utc).timestamp())
        period2 = int(datetime.combine(end + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc).timestamp())
        encoded_symbol = urllib.parse.quote(symbol, safe="")
        params = urllib.parse.urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        payload = self.http.get_json(f"{self.CHART_URL}/{encoded_symbol}?{params}")
        error = payload.get("chart", {}).get("error")
        if error:
            raise RuntimeError(error.get("description") or str(error))
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            raise RuntimeError("Yahoo returned no chart data")
        meta = result.get("meta", {})
        currency = str(meta.get("currency") or "").upper()
        try:
            exchange_tz = ZoneInfo(str(meta.get("exchangeTimezoneName") or "UTC"))
        except ZoneInfoNotFoundError:
            exchange_tz = timezone.utc

        def exchange_day(timestamp: int) -> date:
            return datetime.fromtimestamp(timestamp, tz=exchange_tz).date()

        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators", {})
        adjusted = ((indicators.get("adjclose") or [{}])[0].get("adjclose") or [])
        if not adjusted:
            adjusted = ((indicators.get("quote") or [{}])[0].get("close") or [])
        points = [
            MarketPoint(
                day=exchange_day(timestamp),
                adjusted_close=float(close),
            )
            for timestamp, close in zip(timestamps, adjusted)
            if close is not None
        ]
        market_time: str | None = None
        if include_live and meta.get("regularMarketPrice") is not None and meta.get("regularMarketTime"):
            quote_timestamp = int(meta["regularMarketTime"])
            quote = MarketPoint(exchange_day(quote_timestamp), float(meta["regularMarketPrice"]))
            market_time = datetime.fromtimestamp(quote_timestamp, tz=timezone.utc).isoformat(timespec="seconds")
            if start <= quote.day <= end + timedelta(days=1):
                points = [point for point in points if point.day != quote.day]
                points.append(quote)
        points.sort(key=lambda point: point.day)
        if len(points) < 2:
            raise RuntimeError("Yahoo returned fewer than two price observations")
        return currency, points, market_time

    def eur_returns(
        self,
        symbol: str,
        start: date,
        official_nav_date: date,
        end: date,
        *,
        live: bool,
    ) -> tuple[float, float, float, str, date, date, str | None]:
        currency, prices, market_time = self.history(symbol, start, end, include_live=live)
        currency_aliases = {"GBX": "GBP", "GBPENCE": "GBP", "ZAC": "ZAR"}
        normalized_currency = currency_aliases.get(currency, currency)
        if normalized_currency and normalized_currency != "EUR":
            fx, _ = self._fx_history(normalized_currency, start, end, live=live)
            prices = convert_to_eur(prices, fx)
        disclosure_return, first_day, last_day = return_between(prices, start, end)
        to_official_return, _, _ = return_between(prices, start, official_nav_date)
        if live and official_nav_date < last_day:
            since_official_return, _, _ = return_between(prices, official_nav_date, end)
        else:
            since_official_return = 0.0
        return (
            disclosure_return,
            to_official_return,
            since_official_return,
            currency or "UNKNOWN",
            first_day,
            last_day,
            market_time,
        )

    def _fx_history(
        self,
        currency: str,
        start: date,
        end: date,
        *,
        live: bool,
    ) -> tuple[list[MarketPoint], str | None]:
        key = (currency, start, end, live)
        with self.fx_lock:
            existing = self.fx_cache.get(key)
        if existing is not None:
            return existing
        try:
            _, points, market_time = self.history(
                f"{currency}EUR=X", start, end, include_live=live
            )
        except RuntimeError as direct_error:
            try:
                _, inverse, market_time = self.history(
                    f"EUR{currency}=X", start, end, include_live=live
                )
                points = [
                    MarketPoint(point.day, 1.0 / point.adjusted_close)
                    for point in inverse
                    if point.adjusted_close
                ]
            except RuntimeError:
                try:
                    _, currency_usd, first_time = self.history(
                        f"{currency}USD=X", start, end, include_live=live
                    )
                    _, usd_eur, second_time = self.history(
                        "USDEUR=X", start, end, include_live=live
                    )
                    points = convert_to_eur(currency_usd, usd_eur)
                    market_time = min(
                        (value for value in (first_time, second_time) if value),
                        default=None,
                    )
                except RuntimeError:
                    raise direct_error
        with self.fx_lock:
            self.fx_cache[key] = (points, market_time)
        return points, market_time
