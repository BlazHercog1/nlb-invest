"""Format tracker reports and summarize invested amounts."""
from __future__ import annotations

from typing import Any

from .models import FUNDS


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
    contribution_plans = any(fund.get("investment_plan") for fund in report["funds"])
    lines = [
        "",
        "MY INVESTMENTS",
        "--------------",
        *(
            ["Includes each initial investment and the scheduled monthly contributions shown below."]
            if contribution_plans
            else [f"Based on money invested since: {report['return_since']}"]
        ),
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
        gain = fund["current_value_eur"] - fund["invested_eur"]
        lines.append(
            f"{fund_display_name(fund['key']):<29} {fmt_eur(fund['invested_eur']):>14} "
            f"{fmt_eur(fund['current_value_eur']):>14} "
            f"{fmt_eur(gain):>14}"
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
            investment_label = (
                "total contributions" if fund.get("investment_plan") else "investment"
            )
            lines.append(
                f"{value_label} of your {fmt_eur(fund['invested_eur'])} {investment_label}: "
                f"{fmt_eur(fund['current_value_eur'])}"
            )
        plan = fund.get("investment_plan")
        if plan:
            monthly = (
                f"{fmt_eur(plan['monthly_amount_eur'])} from {plan['monthly_start_date']}"
                if plan["monthly_amount_eur"]
                else "none"
            )
            lines.extend(
                [
                    f"Initial investment: {fmt_eur(plan['initial_amount_eur'])} on {plan['initial_date']} "
                    f"(NAV date used: {plan['initial_nav_date_used']})",
                    f"Monthly contribution: {monthly}",
                    f"Contributions included: {plan['contribution_count']} | "
                    f"accumulated units: {plan['accumulated_units']:.6f}",
                    f"Estimated investment gain / loss: {fmt_eur(plan['estimated_gain_eur'])} "
                    f"({fmt_pct_value(plan['estimated_return_pct'])})",
                ]
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
