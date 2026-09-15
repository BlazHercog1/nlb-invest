"""Convert stock prices into EUR using the latest preceding FX observations."""
from __future__ import annotations

import bisect

from .models import MarketPoint


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
