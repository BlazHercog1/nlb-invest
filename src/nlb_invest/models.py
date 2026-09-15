"""Fund configuration and shared data models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


NLB_BASE_URL = "https://www.nlbskladi.si"


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
