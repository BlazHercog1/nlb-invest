"""Shared HTTP transport with retries and optional cookies."""
from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from typing import Any


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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
