"""Parsing helpers for decimal values supplied by NLB."""
from __future__ import annotations

from typing import Any


def parse_decimal(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace(".", "").replace(",", "."))


def parse_optional_decimal(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return parse_decimal(value)
