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
    pdf: Path,
    since: date,
    amounts: dict,
    live: bool = True,
    investment_plans: dict | None = None,
    *,
    on_progress: Callable[[float, str], None] | None = None,
):
    """Refresh all funds, optionally reporting completed stages to the UI."""
    total_steps = 2 + 2 * len(FUNDS) + 1

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
        if initial_amount < 1000:
            raise ValueError(f"{FUNDS[key].title}: initial investment must be at least EUR 1,000.")
        if monthly_amount and not 40 <= monthly_amount <= 400:
            raise ValueError(f"{FUNDS[key].title}: monthly contribution must be EUR 40 to EUR 400, or zero.")
        if monthly_amount and plan["monthly_start_date"] <= plan["initial_date"]:
            raise ValueError(f"{FUNDS[key].title}: first monthly contribution must be after the initial investment date.")
        for change in plan.get("monthly_changes", []):
            changed_amount = float(change.get("amount_eur") or 0.0)
            if not monthly_amount:
                raise ValueError(f"{FUNDS[key].title}: set a monthly contribution before adding an amount change.")
            if not 40 <= changed_amount <= 400:
                raise ValueError(f"{FUNDS[key].title}: changed monthly contribution must be EUR 40 to EUR 400.")
            if change["effective_date"] <= plan["monthly_start_date"]:
                raise ValueError(f"{FUNDS[key].title}: monthly amount change must be after the first monthly contribution.")
    notify(0, "Reading the monthly PDF...")
    text = extract_pdf_text(pdf)
    notify(1, "Parsing the disclosed fund holdings...")
    parsed = {key: parse_holdings(text, fund) for key, fund in FUNDS.items()}
    nlb, yahoo = NlbClient(), YahooClient(ROOT / "ticker_cache.json")
    reports = []
    for index, (key, fund) in enumerate(FUNDS.items()):
        holdings_date, holdings = parsed[key]
        plan = plans.get(key)
        plan_active = bool(plan and (plan.get("initial_amount_eur") or plan.get("monthly_amount_eur")))
        analysis_start = plan["initial_date"] if plan_active else since
        start = min(
            today - timedelta(days=400), holdings_date - timedelta(days=7),
            analysis_start - timedelta(days=7),
        )
        stage = 2 + 2 * index
        label = f"Fund {index + 1}/{len(FUNDS)}: {fund.title}"
        notify(stage, f"{label}: fetching official NLB values...")
        nav = nlb.get_nav_history(fund, start, today)
        notify(stage + 1, f"{label}: fetching holding prices and FX rates...")
        result = analyze_fund(
            fund, holdings_date, holdings, nav, yahoo, today, 90.0, 6,
            None if plan_active else (amounts.get(key) or None), live, analysis_start,
        )
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
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": today.isoformat(), "source_pdf": pdf.name,
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
    notify(total_steps, "Report saved.")
    return report
