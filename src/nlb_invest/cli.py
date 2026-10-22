"""Command-line options and tracker orchestration."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .analytics import analyze_fund, calculate_contribution_plan
from .models import FUNDS
from .nlb_client import NlbClient
from .pdf_parser import extract_pdf_text, parse_holdings
from .payments import has_payment_plan, normalize_plan, plan_start
from .reporting import build_investment_summary, render_text
from .yahoo_client import YahooClient


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track official NLB fund NAVs and analyze disclosed equity holdings via Yahoo Finance."
    )
    parser.add_argument(
        "--pdf", type=Path, action="append",
        help=(
            "Monthly holdings PDF. Repeat --pdf to apply changing allocations "
            "across multiple months."
        ),
    )
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
    parser.add_argument(
        "--investment-settings",
        type=Path,
        help=(
            "Dashboard settings JSON containing initial and monthly contributions. "
            "Contribution plans take precedence over --invested-* amounts."
        ),
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


def load_investment_plans(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    settings = json.loads(path.read_text(encoding="utf-8"))
    raw_plans = settings.get("plans", {})
    if not isinstance(raw_plans, dict):
        raise ValueError("Investment settings must contain a 'plans' object.")
    plans: dict[str, dict[str, Any]] = {}
    for key, raw_plan in raw_plans.items():
        if key not in FUNDS or not isinstance(raw_plan, dict):
            continue
        plan = normalize_plan(raw_plan)
        plans[key] = plan
    return plans


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pdfs = args.pdf or [Path("Mesecno_porocilo_Julij_2026.pdf")]
    for pdf in pdfs:
        if not pdf.exists():
            print(f"error: PDF not found: {pdf}", file=sys.stderr)
            return 2
    try:
        contribution_plans = load_investment_plans(args.investment_settings)
        selected_funds = args.funds or list(FUNDS)
        parsed = {key: [] for key in selected_funds}
        for pdf in pdfs:
            text = extract_pdf_text(pdf)
            for key in selected_funds:
                holdings_date, holdings = parse_holdings(text, FUNDS[key])
                parsed[key].append((holdings_date, holdings, pdf.name))
        for key in parsed:
            by_date = {
                holdings_date: (holdings, source_name)
                for holdings_date, holdings, source_name in parsed[key]
            }
            parsed[key] = [
                (holdings_date, *by_date[holdings_date])
                for holdings_date in sorted(by_date)
            ]
        nlb = NlbClient()
        yahoo = YahooClient(args.cache, refresh=args.refresh_symbols)
        reports: list[dict[str, Any]] = []
        for key in selected_funds:
            fund = FUNDS[key]
            fund_snapshots = parsed[key]
            holdings_date, holdings, _ = fund_snapshots[-1]
            first_holdings_date = fund_snapshots[0][0]
            nav_start = min(
                args.as_of - timedelta(days=args.nav_days),
                first_holdings_date - timedelta(days=7),
            )
            plan = contribution_plans.get(key)
            plan_active = has_payment_plan(plan)
            analysis_start = args.return_since
            if plan_active:
                analysis_start = plan_start(plan, args.return_since)
                nav_start = min(nav_start, analysis_start - timedelta(days=7))
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
                    None if plan_active else invested_eur,
                    not args.official_close,
                    analysis_start,
                    [
                        (snapshot_date, snapshot_holdings)
                        for snapshot_date, snapshot_holdings, _ in fund_snapshots
                    ],
                )
            )
            if plan_active:
                official_date = date.fromisoformat(
                    reports[-1]["metrics"]["latest_date"]
                )
                investment = calculate_contribution_plan(
                    [point for point in nav if point.day <= official_date],
                    reports[-1]["estimated_live_nav_eur"],
                    plan["initial_amount_eur"],
                    plan["initial_date"],
                    plan["monthly_amount_eur"],
                    plan.get("monthly_start_date"),
                    plan.get("monthly_changes"),
                    plan.get("payments"), as_of=args.as_of,
                )
                reports[-1]["investment_plan"] = investment
                reports[-1]["invested_eur"] = investment["total_contributed_eur"]
                reports[-1]["current_value_eur"] = investment[
                    "estimated_current_value_eur"
                ]
            official_end = date.fromisoformat(
                reports[-1].get("metrics", {}).get("latest_date", args.as_of.isoformat())
            )
            reports[-1]["nav_history"] = [
                {"date": point.day.isoformat(), "nav": point.nav}
                for point in nav if point.day <= official_end
            ]
            sources_by_date = {
                snapshot_date.isoformat(): source_name
                for snapshot_date, _, source_name in fund_snapshots
            }
            for period in reports[-1].get("holding_periods", []):
                period["source_pdf"] = sources_by_date[period["holdings_date"]]
        yahoo.save_cache()
        investment_summary = build_investment_summary(reports)
        source_pdfs = list(dict.fromkeys(
            source_name for _, _, source_name in parsed[selected_funds[0]]
        ))
        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "as_of": args.as_of.isoformat(),
            # Reports may be shared: do not expose the local directory or username.
            "source_pdf": source_pdfs[-1],
            "source_pdfs": source_pdfs,
            "coverage_target_pct": args.coverage,
            "market_mode": "official-close" if args.official_close else "live",
            "return_since": args.return_since.isoformat(),
            "investment_mode": (
                "contribution-plan"
                if any(fund.get("investment_plan") for fund in reports)
                else "lump-sum"
            ),
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
