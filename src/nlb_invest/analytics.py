"""Calculate fund performance, holding contributions and live NAV estimates."""
from __future__ import annotations

import calendar
import concurrent.futures
import math
import statistics
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, TYPE_CHECKING

from .models import FundConfig, Holding, HoldingResult, MarketPoint, NavPoint

if TYPE_CHECKING:
    from .yahoo_client import YahooClient


def add_months(value: date, months: int = 1) -> date:
    """Move a date by whole calendar months, clamping to the month's last day."""
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def calculate_contribution_plan(
    points: list[NavPoint],
    estimated_live_nav: float,
    initial_amount_eur: float,
    initial_date: date,
    monthly_amount_eur: float = 0.0,
    monthly_start_date: date | None = None,
    monthly_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Estimate units bought by one initial payment and fixed monthly payments."""
    if initial_amount_eur < 1000:
        raise ValueError("The initial investment must be at least EUR 1,000.")
    if monthly_amount_eur and not 40 <= monthly_amount_eur <= 400:
        raise ValueError("The monthly contribution must be EUR 40 to EUR 400, or zero.")
    if monthly_amount_eur and monthly_start_date is None:
        raise ValueError("Choose the first monthly contribution date.")
    if monthly_amount_eur and monthly_start_date <= initial_date:
        raise ValueError("The first monthly contribution must be after the initial investment date.")
    normalized_changes = sorted(
        monthly_changes or [], key=lambda change: change["effective_date"]
    )
    for change in normalized_changes:
        amount = float(change["amount_eur"])
        effective_date = change["effective_date"]
        if not monthly_amount_eur:
            raise ValueError("A monthly amount change requires an existing monthly contribution.")
        if not 40 <= amount <= 400:
            raise ValueError("A changed monthly contribution must be EUR 40 to EUR 400.")
        if effective_date <= monthly_start_date:
            raise ValueError("A monthly amount change must take effect after the first monthly contribution.")
    if not points:
        raise RuntimeError("No NLB NAV history is available for the investment plan.")

    official_points = sorted(points, key=lambda point: point.day)
    latest = official_points[-1]

    def purchase(scheduled_date: date, amount: float, kind: str) -> dict[str, Any] | None:
        nav_point = next((point for point in official_points if point.day >= scheduled_date), None)
        if nav_point is None:
            return None
        units = amount / nav_point.nav
        return {
            "type": kind,
            "scheduled_date": scheduled_date.isoformat(),
            "nav_date_used": nav_point.day.isoformat(),
            "amount_eur": amount,
            "nav_eur": nav_point.nav,
            "units": units,
        }

    initial = purchase(initial_date, initial_amount_eur, "initial")
    if initial is None:
        raise RuntimeError(
            f"No published NLB NAV is available on or after the initial investment date {initial_date}."
        )
    contributions = [initial]
    if monthly_amount_eur and monthly_start_date is not None:
        scheduled = monthly_start_date
        month_number = 0
        while scheduled <= latest.day:
            scheduled_amount = monthly_amount_eur
            for change in normalized_changes:
                if change["effective_date"] <= scheduled:
                    scheduled_amount = float(change["amount_eur"])
            contribution = purchase(scheduled, scheduled_amount, "monthly")
            if contribution is not None:
                contributions.append(contribution)
            month_number += 1
            scheduled = add_months(monthly_start_date, month_number)

    total_contributed = sum(item["amount_eur"] for item in contributions)
    accumulated_units = sum(item["units"] for item in contributions)
    official_value = accumulated_units * latest.nav
    estimated_value = accumulated_units * estimated_live_nav
    gain = estimated_value - total_contributed
    value_history = []
    for nav_point in official_points:
        purchased = [
            item for item in contributions
            if date.fromisoformat(item["nav_date_used"]) <= nav_point.day
        ]
        if not purchased:
            continue
        contributed = sum(item["amount_eur"] for item in purchased)
        units = sum(item["units"] for item in purchased)
        value = units * nav_point.nav
        value_history.append({
            "date": nav_point.day.isoformat(),
            "contributed_eur": contributed,
            "value_eur": value,
            "gain_eur": value - contributed,
        })
    return {
        "initial_amount_eur": initial_amount_eur,
        "initial_date": initial_date.isoformat(),
        "initial_nav_date_used": initial["nav_date_used"],
        "monthly_amount_eur": monthly_amount_eur,
        "monthly_start_date": monthly_start_date.isoformat() if monthly_start_date else None,
        "monthly_changes": [
            {
                "effective_date": change["effective_date"].isoformat(),
                "amount_eur": float(change["amount_eur"]),
            }
            for change in normalized_changes
        ],
        "contribution_count": len(contributions),
        "monthly_contribution_count": len(contributions) - 1,
        "total_contributed_eur": total_contributed,
        "accumulated_units": accumulated_units,
        "official_value_eur": official_value,
        "estimated_current_value_eur": estimated_value,
        "estimated_gain_eur": gain,
        "estimated_return_pct": gain / total_contributed * 100.0,
        "contributions": contributions,
        "value_history": value_history,
    }


def select_window(points: list[MarketPoint], start: date, end: date) -> list[MarketPoint]:
    return [point for point in points if start <= point.day <= end]


def return_between(points: list[MarketPoint], start: date, end: date) -> tuple[float, date, date]:
    selected = select_window(points, start, end)
    if len(selected) < 2:
        raise RuntimeError(f"No usable price window between {start} and {end}")
    first, last = selected[0], selected[-1]
    return last.adjusted_close / first.adjusted_close - 1.0, first.day, last.day


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
