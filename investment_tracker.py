#!/usr/bin/env python3
"""Track NLB fund NAVs and attribute changes to disclosed equity holdings."""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import http.cookiejar
import json
import math
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
NLB_BASE_URL = "https://www.nlbskladi.si"
# Yahoo's ISIN search occasionally omits these liquid primary listings or returns
# a thin European cross-listing first. Exact ISIN overrides keep attribution stable.
SYMBOL_OVERRIDES = {
    "KYG3902L1095": "9698.HK",
    "US02079K3059": "GOOGL",
    "US02319V1035": "ABEV",
    "US4655621062": "ITUB",
    "US7960508882": "SMSN.IL",
    "US8740391003": "TSM",
}
COUNTRY_NAMES = sorted(
    {
        "ARGENTINA", "AVSTRALIJA", "AVSTRIJA", "BELGIJA", "BOLGARIJA", "BRAZILIJA",
        "DANSKA", "FINSKA", "FRANCIJA", "HONG KONG", "HRVAŠKA", "INDIJA", "IRSKA",
        "ISLANDIJA", "ITALIJA", "IZRAEL", "JAPONSKA", "JUŽNA AFRIKA", "JUŽNA KOREJA",
        "KANADA", "KITAJSKA", "LUKSEMBURG", "MADŽARSKA", "MEHIKA", "NEMČIJA",
        "NIZOZEMSKA", "NORVEŠKA", "POLJSKA", "PORTUGALSKA", "ROMUNIJA", "SINGAPUR",
        "SLOVENIJA", "ŠPANIJA", "ŠVEDSKA", "ŠVICA", "TAJVAN", "TAJSKA", "URUGVAJ",
        "ZDA", "ZDRUŽENI ARABSKI EMIRATI", "ZDRUŽENO KRALJESTVO",
    },
    key=len,
    reverse=True,
)


@dataclass(frozen=True)
class FundConfig:
    key: str
    title: str
    slug: str
    fund_id: str
    component_path: str

    @property
    def page_url(self) -> str:
        return f"{NLB_BASE_URL}/nalozbene-moznosti/vzajemni-skladi/{self.slug}"


FUNDS = {
    "tech": FundConfig(
        key="tech",
        title="Visoka tehnologija delniški",
        slug="visoka-tehnologija-delniski",
        fund_id="2112",
        component_path=(
            "/content/nlbskladi/nlbskladisi/sl/nalozbene-moznosti/vzajemni-skladi/"
            "visoka-tehnologija-delniski/jcr:content/root/container/"
            "contentcontainer_213/unitvaluecomparator_2000136438"
        ),
    ),
    "balanced": FundConfig(
        key="balanced",
        title="Globalni uravnoteženi",
        slug="globalni-uravnotezeni",
        fund_id="21",
        component_path=(
            "/content/nlbskladi/nlbskladisi/sl/nalozbene-moznosti/vzajemni-skladi/"
            "globalni-uravnotezeni/jcr:content/root/container/"
            "contentcontainer_213/unitvaluecomparator_2000136438"
        ),
    ),
    "developed": FundConfig(
        key="developed",
        title="Svetovni razviti trgi delniški",
        slug="svetovni-razviti-trgi-delniski",
        fund_id="24",
        component_path=(
            "/content/nlbskladi/nlbskladisi/sl/nalozbene-moznosti/vzajemni-skladi/"
            "svetovni-razviti-trgi-delniski/jcr:content/root/container/"
            "contentcontainer_213/unitvaluecomparator_2000136438"
        ),
    ),
}


@dataclass(frozen=True)
class Holding:
    number: int
    isin: str
    issuer: str
    country: str
    weight_pct: float


@dataclass(frozen=True)
class NavPoint:
    day: date
    nav: float
    fund_size: float | None
    trailing: dict[str, float | None]


@dataclass(frozen=True)
class MarketPoint:
    day: date
    adjusted_close: float


@dataclass(frozen=True)
class HoldingResult:
    isin: str
    issuer: str
    country: str
    weight_pct: float
    symbol: str | None
    currency: str | None
    start_date: str | None
    end_date: str | None
    return_pct_eur: float | None
    contribution_pct: float | None
    return_to_official_nav_pct: float | None
    contribution_to_official_nav_pct: float | None
    return_since_official_nav_pct: float | None
    contribution_since_official_nav_pct: float | None
    market_time: str | None
    error: str | None


class HttpClient:
    def __init__(self, use_cookies: bool = False) -> None:
        handlers: list[Any] = []
        if use_cookies:
            handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener = urllib.request.build_opener(*handlers)

    def get_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        attempts: int = 3,
        timeout: int = 30,
    ) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}
        if referer:
            headers["Referer"] = referer
        last_error: Exception | None = None
        for attempt in range(attempts):
            request = urllib.request.Request(url, headers=headers)
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"HTTP request failed for {url}: {last_error}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return json.loads(self.get_bytes(url, **kwargs).decode("utf-8"))


class NlbClient:
    def __init__(self) -> None:
        self.http = HttpClient(use_cookies=True)

    def get_nav_history(self, fund: FundConfig, start: date, end: date) -> list[NavPoint]:
        component_path = fund.component_path
        fund_id = fund.fund_id
        try:
            html = self.http.get_bytes(fund.page_url).decode("utf-8", errors="replace")
            tag_match = re.search(r"<[^>]*js-unit-value-comparator[^>]*>", html, re.IGNORECASE)
            if tag_match:
                tag = tag_match.group(0)
                path_match = re.search(r'data-service-path="([^"]+)"', tag)
                id_match = re.search(r'data-fund-id="([^"]+)"', tag)
                if path_match:
                    component_path = path_match.group(1)
                if id_match:
                    fund_id = id_match.group(1)
        except RuntimeError:
            # The checked-in fallback is intentionally retained for temporary page failures.
            pass

        endpoint = (
            f"{NLB_BASE_URL}{component_path}.fundsarchive.{fund_id}.json?"
            + urllib.parse.urlencode({"dateMin": start.isoformat(), "dateMax": end.isoformat()})
        )
        raw = self.http.get_json(endpoint, referer=fund.page_url)
        points: list[NavPoint] = []
        for entry in raw:
            matching = next((item for item in entry.get("funds", []) if str(item.get("id")) == fund_id), None)
            if not matching:
                continue
            points.append(
                NavPoint(
                    day=date.fromisoformat(entry["date"]),
                    nav=parse_decimal(matching.get("nav4") or matching.get("nav")),
                    fund_size=parse_optional_decimal(matching.get("subfundSize")),
                    trailing={
                        "6m": parse_optional_decimal(matching.get("deltaNav6m")),
                        "12m": parse_optional_decimal(matching.get("deltaNav12m")),
                        "36m": parse_optional_decimal(matching.get("deltaNav36m")),
                        "60m": parse_optional_decimal(matching.get("deltaNav60m")),
                    },
                )
            )
        if not points:
            raise RuntimeError(f"NLB returned no NAV data for {fund.title}")
        return sorted(points, key=lambda point: point.day)


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


def parse_decimal(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace(".", "").replace(",", "."))


def parse_optional_decimal(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return parse_decimal(value)


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "The Python package pypdf is required. Install it with: "
            "py -3 -m pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(pdf_path)
        pages: list[str] = []
        for page in reader.pages:
            try:
                page_text = page.extract_text(extraction_mode="layout")
            except TypeError:
                # Compatibility with older pypdf versions that lack layout mode.
                page_text = page.extract_text()
            pages.append(page_text or "")
    except Exception as exc:
        raise RuntimeError(f"Could not read PDF {pdf_path}: {exc}") from exc

    return "\n\f\n".join(pages).replace("–", "-").replace("—", "-")


def parse_holdings(text: str, fund: FundConfig) -> tuple[date, list[Holding]]:
    table_header = "Naložbe podsklada v delnice in enote delniških ciljnih skladov na dan"
    table_positions = [match.start() for match in re.finditer(re.escape(table_header), text)]
    fund_header_re = re.compile(r"(?m)^\s*NLB Skladi\s+-\s+([^\r\n]+?)\s*$")
    headers = list(fund_header_re.finditer(text))
    table_pos: int | None = None
    for candidate in table_positions:
        previous_headers = [match for match in headers if match.start() < candidate]
        if previous_headers and fund.title.casefold() in previous_headers[-1].group(1).casefold():
            table_pos = candidate
            break
    if table_pos is None:
        raise RuntimeError(f"Could not locate the equity holdings table for {fund.title}")

    date_match = re.search(r"na dan\s+(\d{2}\.\d{2}\.\d{4})", text[table_pos : table_pos + 300])
    if not date_match:
        raise RuntimeError(f"Could not determine the holdings date for {fund.title}")
    holdings_date = datetime.strptime(date_match.group(1), "%d.%m.%Y").date()

    row_re = re.compile(r"(?m)^\s*(\d+)\s+([A-Z]{2}[A-Z0-9]{10})\s+")
    row_matches = list(row_re.finditer(text, table_pos))
    holdings: list[Holding] = []
    expected_number = 1
    for index, match in enumerate(row_matches):
        number = int(match.group(1))
        if not holdings and number != 1:
            continue
        if holdings and number != expected_number:
            break
        body_end = row_matches[index + 1].start() if index + 1 < len(row_matches) else len(text)
        body = text[match.end() : body_end]
        weight_match = re.search(r"(\d+),((?:\s*\d)+)\s*%", body)
        if not weight_match:
            if holdings:
                break
            continue
        raw_identity = body[: weight_match.start()].replace("\f", " ").strip()
        identity = re.sub(r"\s+", " ", raw_identity).strip()
        country = next((name for name in COUNTRY_NAMES if identity.endswith(f" {name}")), None)
        if country is None:
            pieces = [re.sub(r"\s+", " ", piece).strip() for piece in re.split(r"\s{2,}", raw_identity) if piece.strip()]
            country = pieces[-1] if len(pieces) >= 2 else None
        if not country:
            raise RuntimeError(f"Could not parse issuer/country for row {number} in {fund.title}")
        issuer = identity[: -len(country)].strip()
        fractional_digits = re.sub(r"\D", "", weight_match.group(2))
        weight = float(f"{weight_match.group(1)}.{fractional_digits}")
        holdings.append(Holding(number, match.group(2), issuer, country, weight))
        expected_number = number + 1

    if not holdings:
        raise RuntimeError(f"No holdings parsed for {fund.title}")
    return holdings_date, holdings


def select_window(points: list[MarketPoint], start: date, end: date) -> list[MarketPoint]:
    return [point for point in points if start <= point.day <= end]


def return_between(points: list[MarketPoint], start: date, end: date) -> tuple[float, date, date]:
    selected = select_window(points, start, end)
    if len(selected) < 2:
        raise RuntimeError(f"No usable price window between {start} and {end}")
    first, last = selected[0], selected[-1]
    return last.adjusted_close / first.adjusted_close - 1.0, first.day, last.day


def convert_to_eur(prices: list[MarketPoint], fx: list[MarketPoint]) -> list[MarketPoint]:
    fx_days = [point.day for point in fx]
    converted: list[MarketPoint] = []
    for point in prices:
        index = bisect.bisect_right(fx_days, point.day) - 1
        if index >= 0:
            converted.append(MarketPoint(point.day, point.adjusted_close * fx[index].adjusted_close))
    if len(converted) < 2:
        raise RuntimeError("No matching FX observations")
    return converted


def nav_metrics(points: list[NavPoint], as_of: date, holdings_date: date) -> dict[str, Any]:
    usable = [point for point in points if point.day <= as_of]
    if len(usable) < 2:
        raise RuntimeError("Insufficient NLB NAV history")
    latest = usable[-1]

    def period_return(target: date) -> float | None:
        prior = [point for point in usable if point.day <= target]
        baseline = prior[-1] if prior else usable[0]
        if baseline.day >= latest.day:
            return None
        return latest.nav / baseline.nav - 1.0

    daily_returns = [usable[i].nav / usable[i - 1].nav - 1.0 for i in range(1, len(usable))]
    volatility = statistics.stdev(daily_returns) * math.sqrt(252) if len(daily_returns) >= 2 else None
    peak = usable[0].nav
    max_drawdown = 0.0
    for point in usable:
        peak = max(peak, point.nav)
        max_drawdown = min(max_drawdown, point.nav / peak - 1.0)
    return {
        "latest_date": latest.day.isoformat(),
        "latest_nav_eur": latest.nav,
        "fund_size_eur": latest.fund_size,
        "since_holdings": period_return(holdings_date),
        "one_month": period_return(latest.day - timedelta(days=30)),
        "three_month": period_return(latest.day - timedelta(days=91)),
        "ytd": period_return(date(latest.day.year - 1, 12, 31)),
        "one_year": period_return(latest.day - timedelta(days=365)),
        "annualized_volatility": volatility,
        "max_drawdown": max_drawdown,
        "trailing_nlb": latest.trailing,
    }


def calculate_since_date_analysis(
    points: list[NavPoint],
    requested_date: date,
    estimated_live_nav: float,
    invested_eur: float | None,
) -> dict[str, Any]:
    if not points:
        raise RuntimeError("No NLB NAV history is available for return-since analysis")
    latest = points[-1]
    candidates = [point for point in points if point.day <= requested_date]
    if not candidates:
        raise RuntimeError(f"No NLB NAV is available on or before {requested_date}")
    anchor = candidates[-1]
    official_return = latest.nav / anchor.nav - 1.0
    estimated_live_return = estimated_live_nav / anchor.nav - 1.0
    return {
        "requested_date": requested_date.isoformat(),
        "nav_date_used": anchor.day.isoformat(),
        "starting_nav_eur": anchor.nav,
        "official_end_date": latest.day.isoformat(),
        "official_end_nav_eur": latest.nav,
        "official_return_pct": official_return * 100.0,
        "estimated_live_nav_eur": estimated_live_nav,
        "estimated_live_return_pct": estimated_live_return * 100.0,
        "estimated_gain_per_1000_eur": 1000.0 * estimated_live_return,
        "invested_eur": invested_eur,
        "estimated_current_value_for_investment_eur": (
            invested_eur * (1.0 + estimated_live_return) if invested_eur is not None else None
        ),
        "estimated_gain_for_investment_eur": (
            invested_eur * estimated_live_return if invested_eur is not None else None
        ),
    }


def choose_holdings(holdings: list[Holding], coverage_target: float) -> list[Holding]:
    total = sum(holding.weight_pct for holding in holdings)
    target = total * coverage_target / 100.0
    chosen: list[Holding] = []
    cumulative = 0.0
    for holding in sorted(holdings, key=lambda item: item.weight_pct, reverse=True):
        chosen.append(holding)
        cumulative += holding.weight_pct
        if cumulative >= target:
            break
    return chosen


def analyze_holding(
    yahoo: YahooClient,
    holding: Holding,
    start: date,
    official_nav_date: date,
    end: date,
    live: bool,
) -> HoldingResult:
    symbol = yahoo.resolve_isin(holding)
    if not symbol:
        return HoldingResult(
            isin=holding.isin,
            issuer=holding.issuer,
            country=holding.country,
            weight_pct=holding.weight_pct,
            symbol=None,
            currency=None,
            start_date=None,
            end_date=None,
            return_pct_eur=None,
            contribution_pct=None,
            return_to_official_nav_pct=None,
            contribution_to_official_nav_pct=None,
            return_since_official_nav_pct=None,
            contribution_since_official_nav_pct=None,
            market_time=None,
            error="No Yahoo symbol found",
        )
    try:
        (
            result,
            result_to_official,
            result_since_official,
            currency,
            first_day,
            last_day,
            market_time,
        ) = yahoo.eur_returns(symbol, start, official_nav_date, end, live=live)
        return HoldingResult(
            isin=holding.isin,
            issuer=holding.issuer,
            country=holding.country,
            weight_pct=holding.weight_pct,
            symbol=symbol,
            currency=currency,
            start_date=first_day.isoformat(),
            end_date=last_day.isoformat(),
            return_pct_eur=result * 100.0,
            contribution_pct=holding.weight_pct * result,
            return_to_official_nav_pct=result_to_official * 100.0,
            contribution_to_official_nav_pct=holding.weight_pct * result_to_official,
            return_since_official_nav_pct=result_since_official * 100.0,
            contribution_since_official_nav_pct=holding.weight_pct * result_since_official,
            market_time=market_time,
            error=None,
        )
    except Exception as exc:
        return HoldingResult(
            isin=holding.isin,
            issuer=holding.issuer,
            country=holding.country,
            weight_pct=holding.weight_pct,
            symbol=symbol,
            currency=None,
            start_date=None,
            end_date=None,
            return_pct_eur=None,
            contribution_pct=None,
            return_to_official_nav_pct=None,
            contribution_to_official_nav_pct=None,
            return_since_official_nav_pct=None,
            contribution_since_official_nav_pct=None,
            market_time=None,
            error=str(exc),
        )


def analyze_fund(
    fund: FundConfig,
    holdings_date: date,
    holdings: list[Holding],
    nav: list[NavPoint],
    yahoo: YahooClient,
    as_of: date,
    coverage_target: float,
    workers: int,
    invested_eur: float | None,
    live: bool,
    return_since: date,
) -> dict[str, Any]:
    metrics = nav_metrics(nav, as_of, holdings_date)
    official_nav_date = date.fromisoformat(metrics["latest_date"])
    market_end = as_of if live else min(as_of, official_nav_date)
    selected = choose_holdings(holdings, coverage_target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                analyze_holding,
                yahoo,
                item,
                holdings_date,
                official_nav_date,
                market_end,
                live,
            )
            for item in selected
        ]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    results.sort(key=lambda item: item.weight_pct, reverse=True)
    resolved = [result for result in results if result.return_pct_eur is not None]
    tracked_weight = sum(result.weight_pct for result in resolved)
    selected_weight = sum(item.weight_pct for item in selected)
    disclosed_weight = sum(item.weight_pct for item in holdings)
    contribution = sum(result.contribution_pct or 0.0 for result in resolved)
    aligned_contribution = sum(result.contribution_to_official_nav_pct or 0.0 for result in resolved)
    live_contribution = sum(result.contribution_since_official_nav_pct or 0.0 for result in resolved)
    normalized_return = contribution / tracked_weight * 100.0 if tracked_weight else None
    actual_return_pct = (
        metrics["since_holdings"] * 100.0 if metrics["since_holdings"] is not None else None
    )
    estimated_live_nav = metrics["latest_nav_eur"] * (1.0 + live_contribution / 100.0)
    estimated_live_return = (
        ((1.0 + actual_return_pct / 100.0) * (1.0 + live_contribution / 100.0) - 1.0) * 100.0
        if actual_return_pct is not None
        else None
    )
    since_date_analysis = calculate_since_date_analysis(
        [point for point in nav if point.day <= official_nav_date],
        return_since,
        estimated_live_nav,
        invested_eur,
    )
    current_value = since_date_analysis["estimated_current_value_for_investment_eur"]
    market_times = sorted(result.market_time for result in resolved if result.market_time)
    return {
        "key": fund.key,
        "name": f"NLB Skladi - {fund.title}",
        "holdings_date": holdings_date.isoformat(),
        "position_count": len(holdings),
        "disclosed_equity_weight_pct": disclosed_weight,
        "selected_position_count": len(selected),
        "selected_weight_pct": selected_weight,
        "tracked_weight_pct": tracked_weight,
        "tracked_share_of_disclosed_pct": tracked_weight / disclosed_weight * 100.0 if disclosed_weight else 0.0,
        "market_mode": "live" if live else "official-close",
        "market_time_earliest": market_times[0] if market_times else None,
        "market_time_latest": market_times[-1] if market_times else None,
        "estimated_contribution_pct": contribution,
        "aligned_contribution_pct": aligned_contribution,
        "live_contribution_since_official_nav_pct": live_contribution,
        "estimated_live_nav_eur": estimated_live_nav,
        "estimated_live_return_since_holdings_pct": estimated_live_return,
        "normalized_tracked_holdings_return_pct": normalized_return,
        "official_return_since_holdings_pct": actual_return_pct,
        "tracking_gap_pct": actual_return_pct - aligned_contribution if actual_return_pct is not None else None,
        "metrics": metrics,
        "invested_eur": invested_eur,
        "current_value_eur": current_value,
        "return_since_date": since_date_analysis,
        "holdings": [asdict(result) for result in results],
    }


def fmt_pct(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value * 100:+.{decimals}f}%"


def fmt_pct_value(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value:+.{decimals}f}%"


def fmt_eur(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"EUR {value:,.{decimals}f}"


def fund_display_name(key: str) -> str:
    fund = FUNDS.get(key)
    return fund.title if fund else key


def build_investment_summary(funds: list[dict[str, Any]]) -> dict[str, Any] | None:
    invested_funds = [fund for fund in funds if fund["invested_eur"] is not None]
    if not invested_funds:
        return None
    total_invested = sum(fund["invested_eur"] or 0.0 for fund in invested_funds)
    total_current = sum(fund["current_value_eur"] or 0.0 for fund in invested_funds)
    total_gain = total_current - total_invested
    return {
        "total_invested_eur": total_invested,
        "estimated_current_value_eur": total_current,
        "estimated_gain_eur": total_gain,
        "estimated_return_pct": (total_gain / total_invested * 100.0 if total_invested else None),
    }


def render_investment_summary(report: dict[str, Any]) -> list[str]:
    summary = report["investment_summary"]
    lines = [
        "",
        "MY INVESTMENTS",
        "--------------",
        f"Based on money invested since: {report['return_since']}",
        f"Total invested:                {fmt_eur(summary['total_invested_eur'])}",
        f"Estimated current value:       {fmt_eur(summary['estimated_current_value_eur'])}",
        f"Estimated gain / loss:         {fmt_eur(summary['estimated_gain_eur'])}",
        f"Estimated return:              {fmt_pct_value(summary['estimated_return_pct'])}",
        "",
        f"{'Fund':<29} {'Invested':>14} {'Value now':>14} {'Gain/Loss':>14}",
    ]
    for fund in report["funds"]:
        if fund["invested_eur"] is None:
            continue
        since = fund["return_since_date"]
        lines.append(
            f"{fund_display_name(fund['key']):<29} {fmt_eur(fund['invested_eur']):>14} "
            f"{fmt_eur(fund['current_value_eur']):>14} "
            f"{fmt_eur(since['estimated_gain_for_investment_eur']):>14}"
        )
    return lines


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "NLB INVESTMENT TRACKER",
        "======================",
        f"Generated: {report['generated_at']}",
    ]
    if report.get("investment_summary"):
        lines.extend(render_investment_summary(report))
    lines.extend(["", "FUND DETAILS", "------------"])
    for fund in report["funds"]:
        metrics = fund["metrics"]
        live = fund["market_mode"] == "live"
        lines.extend(
            [
                "",
                fund["name"],
                "-" * len(fund["name"]),
                f"Official NLB NAV: {fmt_eur(metrics['latest_nav_eur'], 4)} on {metrics['latest_date']}",
                *(
                    [
                        f"Estimated live NAV: {fmt_eur(fund['estimated_live_nav_eur'], 4)}",
                        f"Estimated move since official NAV: "
                        f"{fmt_pct_value(fund['live_contribution_since_official_nav_pct'])}",
                        f"Yahoo last-trade times (UTC): {fund['market_time_earliest'] or 'n/a'} "
                        f"to {fund['market_time_latest'] or 'n/a'}",
                    ]
                    if live
                    else []
                ),
                f"Official return since disclosed holdings ({fund['holdings_date']}): "
                f"{fmt_pct_value(fund['official_return_since_holdings_pct'])}",
                *(
                    [
                        f"Estimated live return since disclosed holdings: "
                        f"{fmt_pct_value(fund['estimated_live_return_since_holdings_pct'])}"
                    ]
                    if live
                    else []
                ),
                f"1m {fmt_pct(metrics['one_month'])} | 3m {fmt_pct(metrics['three_month'])} | "
                f"YTD {fmt_pct(metrics['ytd'])} | 1y {fmt_pct(metrics['one_year'])}",
                f"Annualized daily volatility {fmt_pct(metrics['annualized_volatility'])} | "
                f"max drawdown in analysis window {fmt_pct(metrics['max_drawdown'])}",
                f"Fund size: {fmt_eur(metrics['fund_size_eur'], 0)}",
            ]
        )
        if fund["invested_eur"] is not None:
            value_label = "Estimated live value" if live else "Value at official NAV"
            lines.append(
                f"{value_label} of your {fmt_eur(fund['invested_eur'])} investment: "
                f"{fmt_eur(fund['current_value_eur'])}"
            )
        since = fund["return_since_date"]
        end_label = "Estimated live" if live else "Official close"
        lines.extend(
            [
                "",
                f"RETURN / EARNINGS SINCE {since['requested_date']}",
                f"Starting NLB NAV: {fmt_eur(since['starting_nav_eur'], 4)} "
                f"on {since['nav_date_used']}",
                f"Official return through {since['official_end_date']}: "
                f"{fmt_pct_value(since['official_return_pct'])}",
                f"{end_label} return: {fmt_pct_value(since['estimated_live_return_pct'])}",
                f"Estimated gain per EUR 1,000 invested: "
                f"{fmt_eur(since['estimated_gain_per_1000_eur'])}",
            ]
        )
        if since["estimated_gain_for_investment_eur"] is not None:
            lines.append(
                f"Estimated gain for your {fmt_eur(fund['invested_eur'])} investment: "
                f"{fmt_eur(since['estimated_gain_for_investment_eur'])}"
            )
        lines.extend(
            [
                "",
                f"Holdings attribution: {fund['position_count']} PDF positions, "
                f"{fund['disclosed_equity_weight_pct']:.2f}% of fund NAV disclosed as equities.",
                f"Market data covers {fund['tracked_weight_pct']:.2f}% of fund NAV "
                f"({fund['tracked_share_of_disclosed_pct']:.1f}% of disclosed equities).",
                f"Equity contribution through official NAV date: "
                f"{fmt_pct_value(fund['aligned_contribution_pct'])}",
                f"Aligned gap versus official fund return: {fmt_pct_value(fund['tracking_gap_pct'])}",
                *(
                    [
                        f"Equity contribution since official NAV date (live): "
                        f"{fmt_pct_value(fund['live_contribution_since_official_nav_pct'])}",
                        f"Equity contribution from disclosure date to live: "
                        f"{fmt_pct_value(fund['estimated_contribution_pct'])}",
                    ]
                    if live
                    else []
                ),
            ]
        )
        contribution_key = "contribution_since_official_nav_pct" if live else "contribution_pct"
        return_key = "return_since_official_nav_pct" if live else "return_pct_eur"
        valid = [item for item in fund["holdings"] if item[contribution_key] is not None]
        contributors = sorted(valid, key=lambda item: item[contribution_key], reverse=True)
        period_label = "since official NAV" if live else "since disclosure"
        lines.extend(["", f"All contributors {period_label} (EUR-adjusted):"])
        for item in contributors:
            lines.append(
                f"  {item['symbol']:<12} {item['issuer'][:35]:<35} "
                f"w {item['weight_pct']:>5.2f}% | return {item[return_key]:>+7.2f}% | "
                f"contribution {item[contribution_key]:>+6.2f} pp"
            )
        unresolved = [item for item in fund["holdings"] if item["error"]]
        if unresolved:
            missing_weight = sum(item["weight_pct"] for item in unresolved)
            lines.append(
                f"\nUnresolved/failed selected positions: {len(unresolved)} ({missing_weight:.2f}% weight). "
                "See JSON output or ticker_cache.json for details."
            )
    lines.extend(
        [
            "",
            "Interpretation",
            "--------------",
            "Official NLB NAV is the source of truth. Holdings attribution is an estimate using",
            "the fixed weights in the supplied monthly PDF, adjusted into EUR. It cannot capture",
            "later trades, fees, cash, derivatives, or the balanced fund's direct bond sleeve.",
        ]
    )
    if report["market_mode"] == "live":
        lines.extend(
            [
                "The live NAV estimate assumes untracked assets and bonds are unchanged since NLB's",
                "latest NAV. Yahoo values are latest available trades and may be delayed or from a",
                "closed exchange; they are not simultaneous executable prices.",
            ]
        )
    lines.extend(
        [
            "Yahoo's public endpoints are unofficial and may occasionally rate-limit or remap a listing.",
            "Return-since estimates assume continuous ownership and exclude taxes, charges, deposits, and withdrawals.",
            "This report is informational analysis, not investment advice.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track official NLB fund NAVs and analyze disclosed equity holdings via Yahoo Finance."
    )
    parser.add_argument("--pdf", type=Path, default=Path("Mesecno_porocilo_Julij_2026.pdf"))
    parser.add_argument("--fund", action="append", choices=sorted(FUNDS), dest="funds")
    parser.add_argument("--coverage", type=float, default=90.0, help="Target share of disclosed equities to query")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent Yahoo requests")
    parser.add_argument("--nav-days", type=int, default=400, help="NLB NAV history window")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--return-since",
        type=date.fromisoformat,
        default=date(2026, 8, 19),
        help="Start date for the dedicated earnings/return section (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--invested-tech",
        type=float,
        help="Money invested in the technology fund on the return-since date",
    )
    parser.add_argument(
        "--invested-balanced",
        type=float,
        help="Money invested in the balanced fund on the return-since date",
    )
    parser.add_argument(
        "--invested-developed",
        type=float,
        help="Money invested in the developed-markets fund on the return-since date",
    )
    parser.add_argument("--cache", type=Path, default=Path("ticker_cache.json"))
    parser.add_argument("--refresh-symbols", action="store_true")
    parser.add_argument(
        "--official-close",
        action="store_true",
        help="Stop Yahoo prices at NLB's latest official NAV date instead of estimating live NAV",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--latest-report",
        type=Path,
        default=Path("latest_report.txt"),
        help="Text dashboard refreshed after every successful run (default: latest_report.txt)",
    )
    parser.add_argument("--output", type=Path, help="Also write the complete report to this file")
    args = parser.parse_args(argv)
    if not 0 < args.coverage <= 100:
        parser.error("--coverage must be in (0, 100]")
    if args.workers < 1 or args.workers > 16:
        parser.error("--workers must be between 1 and 16")
    if args.invested_tech is not None and args.invested_tech < 0:
        parser.error("--invested-tech cannot be negative")
    if args.invested_balanced is not None and args.invested_balanced < 0:
        parser.error("--invested-balanced cannot be negative")
    if args.invested_developed is not None and args.invested_developed < 0:
        parser.error("--invested-developed cannot be negative")
    if (
        args.format == "json"
        and args.output is not None
        and args.output.resolve() == args.latest_report.resolve()
    ):
        parser.error("--output must differ from --latest-report in JSON mode")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.pdf.exists():
        print(f"error: PDF not found: {args.pdf}", file=sys.stderr)
        return 2
    try:
        text = extract_pdf_text(args.pdf)
        selected_funds = args.funds or list(FUNDS)
        parsed = {key: parse_holdings(text, FUNDS[key]) for key in selected_funds}
        nlb = NlbClient()
        yahoo = YahooClient(args.cache, refresh=args.refresh_symbols)
        reports: list[dict[str, Any]] = []
        for key in selected_funds:
            fund = FUNDS[key]
            holdings_date, holdings = parsed[key]
            nav_start = min(args.as_of - timedelta(days=args.nav_days), holdings_date - timedelta(days=7))
            nav = nlb.get_nav_history(fund, nav_start, args.as_of)
            invested_eur = {
                "tech": args.invested_tech,
                "balanced": args.invested_balanced,
                "developed": args.invested_developed,
            }[key]
            reports.append(
                analyze_fund(
                    fund,
                    holdings_date,
                    holdings,
                    nav,
                    yahoo,
                    args.as_of,
                    args.coverage,
                    args.workers,
                    invested_eur,
                    not args.official_close,
                    args.return_since,
                )
            )
        yahoo.save_cache()
        investment_summary = build_investment_summary(reports)
        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "as_of": args.as_of.isoformat(),
            # Reports may be shared: do not expose the local directory or username.
            "source_pdf": args.pdf.name,
            "coverage_target_pct": args.coverage,
            "market_mode": "official-close" if args.official_close else "live",
            "return_since": args.return_since.isoformat(),
            "investment_summary": investment_summary,
            "funds": reports,
        }
        text_output = render_text(report)
        output = (
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.format == "json"
            else text_output
        )
        args.latest_report.write_text(text_output, encoding="utf-8")
        if args.output:
            same_as_latest = args.output.resolve() == args.latest_report.resolve()
            if not same_as_latest:
                args.output.write_text(output, encoding="utf-8")
        sys.stdout.write(output)
        return 0
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
