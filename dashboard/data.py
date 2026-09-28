"""Local dashboard storage and an adapter to the existing tracker."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from nlb_invest.analytics import analyze_fund, calculate_contribution_plan
from nlb_invest.models import FUNDS
from nlb_invest.nlb_client import NlbClient
from nlb_invest.pdf_parser import extract_pdf_text, parse_holdings
from nlb_invest.reporting import build_investment_summary, render_text
from nlb_invest.yahoo_client import YahooClient

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "local"
REPORT_PATH = LOCAL / "dashboard_report.json"
SETTINGS_PATH = LOCAL / "dashboard_settings.json"
TEXT_REPORT_PATH = ROOT / "latest_report.txt"
DEFAULT_DATE = date(2026, 8, 19)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def load_report():
    report = read_json(REPORT_PATH) or read_json(ROOT / "latest_report.json")
    return report if isinstance(report, dict) and isinstance(report.get("funds"), list) else None


def load_settings():
    saved = read_json(SETTINGS_PATH)
    if isinstance(saved, dict):
        return saved
    report = load_report() or {}
    return {
        "return_since": report.get("return_since", DEFAULT_DATE.isoformat()),
        "amounts": {f["key"]: f.get("invested_eur") or 0.0 for f in report.get("funds", [])},
    }


def report_exports(report: dict) -> tuple[bytes, bytes]:
    """Serialize the displayed report for UTF-8 text and JSON downloads."""
    text = render_text(report).encode("utf-8")
    json_data = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return text, json_data


def refresh_report(
    pdf: Path | list[Path],
    since: date,
    amounts: dict,
    live: bool = True,
    investment_plans: dict | None = None,
    *,
    on_progress: Callable[[float, str], None] | None = None,
):
    """Refresh all funds, optionally reporting completed stages to the UI."""
    pdfs = [pdf] if isinstance(pdf, Path) else list(pdf)
    if not pdfs:
        raise ValueError("Choose at least one monthly holdings report.")
    total_steps = len(pdfs) + 1 + 2 * len(FUNDS) + 1

    def notify(completed: int, message: str) -> None:
        if on_progress is not None:
            on_progress(completed / total_steps, message)

    today = date.today()
    if since > today:
        raise ValueError("The investment start date cannot be in the future.")
    if any(value < 0 for value in amounts.values()):
        raise ValueError("Invested amounts cannot be negative.")
    plans = investment_plans or {}
    for key, plan in plans.items():
        initial_amount = float(plan.get("initial_amount_eur") or 0.0)
        monthly_amount = float(plan.get("monthly_amount_eur") or 0.0)
        if initial_amount == 0 and monthly_amount == 0:
            continue
        if initial_amount and initial_amount < 1000:
            raise ValueError(
                f"{FUNDS[key].title}: one-time initial investment must be at least EUR 1,000, or zero."
            )
        if monthly_amount and not 40 <= monthly_amount <= 500:
            raise ValueError(
                f"{FUNDS[key].title}: savings-plan contribution must be EUR 40 to EUR 500, or zero."
            )
        if (
            initial_amount and monthly_amount
            and plan["monthly_start_date"] <= plan["initial_date"]
        ):
            raise ValueError(
                f"{FUNDS[key].title}: first savings-plan contribution must be after "
                "the initial investment date."
            )
        for change in plan.get("monthly_changes", []):
            changed_amount = float(change.get("amount_eur") or 0.0)
            if not monthly_amount:
                raise ValueError(f"{FUNDS[key].title}: set a monthly contribution before adding an amount change.")
            if not 40 <= changed_amount <= 500:
                raise ValueError(
                    f"{FUNDS[key].title}: changed savings-plan contribution "
                    "must be EUR 40 to EUR 500."
                )
            if change["effective_date"] <= plan["monthly_start_date"]:
                raise ValueError(f"{FUNDS[key].title}: monthly amount change must be after the first monthly contribution.")
    parsed = {key: [] for key in FUNDS}
    for pdf_index, pdf_path in enumerate(pdfs):
        notify(pdf_index, f"Reading monthly PDF {pdf_index + 1}/{len(pdfs)}...")
        text = extract_pdf_text(pdf_path)
        for key, fund in FUNDS.items():
            holdings_date, holdings = parse_holdings(text, fund)
            parsed[key].append((holdings_date, holdings, pdf_path.name))
    notify(len(pdfs), "Ordering the monthly holdings snapshots...")
    for key in parsed:
        by_date = {
            holdings_date: (holdings, source_name)
            for holdings_date, holdings, source_name in parsed[key]
        }
        parsed[key] = [
            (holdings_date, *by_date[holdings_date])
            for holdings_date in sorted(by_date)
        ]
    nlb, yahoo = NlbClient(), YahooClient(ROOT / "ticker_cache.json")
    reports = []
    for index, (key, fund) in enumerate(FUNDS.items()):
        fund_snapshots = parsed[key]
        holdings_date, holdings, _ = fund_snapshots[-1]
        plan = plans.get(key)
        plan_active = bool(
            plan and (plan.get("initial_amount_eur") or plan.get("monthly_amount_eur"))
        )
        if plan_active:
            contribution_dates = []
            if plan.get("initial_amount_eur"):
                contribution_dates.append(plan["initial_date"])
            if plan.get("monthly_amount_eur"):
                contribution_dates.append(plan["monthly_start_date"])
            analysis_start = min(contribution_dates)
        else:
            analysis_start = since
        start = min(
            today - timedelta(days=400), fund_snapshots[0][0] - timedelta(days=7),
            analysis_start - timedelta(days=7),
        )
        stage = len(pdfs) + 1 + 2 * index
        label = f"Fund {index + 1}/{len(FUNDS)}: {fund.title}"
        notify(stage, f"{label}: fetching official NLB values...")
        nav = nlb.get_nav_history(fund, start, today)
        notify(stage + 1, f"{label}: fetching holding prices and FX rates...")
        result = analyze_fund(
            fund, holdings_date, holdings, nav, yahoo, today, 90.0, 6,
            None if plan_active else (amounts.get(key) or None), live, analysis_start,
            [(snapshot_date, snapshot_holdings) for snapshot_date, snapshot_holdings, _ in fund_snapshots],
        )
        sources_by_date = {
            snapshot_date.isoformat(): source_name
            for snapshot_date, _, source_name in fund_snapshots
        }
        for period in result["holding_periods"]:
            period["source_pdf"] = sources_by_date[period["holdings_date"]]
        official_date = date.fromisoformat(result["metrics"]["latest_date"])
        if plan_active:
            investment = calculate_contribution_plan(
                [point for point in nav if point.day <= official_date],
                result["estimated_live_nav_eur"],
                float(plan["initial_amount_eur"]),
                plan["initial_date"],
                float(plan.get("monthly_amount_eur") or 0.0),
                plan.get("monthly_start_date"),
                plan.get("monthly_changes"),
            )
            result["investment_plan"] = investment
            result["invested_eur"] = investment["total_contributed_eur"]
            result["current_value_eur"] = investment["estimated_current_value_eur"]
        result["nav_history"] = [
            {"date": point.day.isoformat(), "nav": point.nav}
            for point in nav if point.day <= official_date
        ]
        reports.append(result)
        notify(stage + 2, f"{label}: complete")
    notify(total_steps - 1, "Saving the updated report...")
    yahoo.save_cache()
    source_pdfs = list(dict.fromkeys(
        source_name for _, _, source_name in parsed[next(iter(FUNDS))]
    ))
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": today.isoformat(), "source_pdf": source_pdfs[-1],
        "source_pdfs": source_pdfs,
        "return_since": since.isoformat(), "market_mode": "live" if live else "official-close",
        "investment_mode": (
            "contribution-plan"
            if any(
                plan.get("initial_amount_eur") or plan.get("monthly_amount_eur")
                for plan in plans.values()
            )
            else "lump-sum"
        ),
        "coverage_target_pct": 90.0,
        "investment_summary": build_investment_summary(reports), "funds": reports,
    }
    write_json(REPORT_PATH, report)
    write_text(TEXT_REPORT_PATH, render_text(report))
    notify(total_steps, "Report saved.")
    return report
