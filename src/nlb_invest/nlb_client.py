"""Fetch official fund NAV history from NLB."""
from __future__ import annotations

import re
import urllib.parse
from datetime import date

from .http_client import HttpClient
from .models import NLB_BASE_URL, FundConfig, NavPoint
from .utils import parse_decimal, parse_optional_decimal


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
