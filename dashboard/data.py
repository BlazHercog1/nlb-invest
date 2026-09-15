"""Local dashboard storage and an adapter to the existing tracker."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from nlb_invest.analytics import analyze_fund
from nlb_invest.models import FUNDS
from nlb_invest.nlb_client import NlbClient
from nlb_invest.pdf_parser import extract_pdf_text, parse_holdings
from nlb_invest.reporting import build_investment_summary
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


def refresh_report(pdf: Path, since: date, amounts: dict, live: bool = True):
    today = date.today()
    if since > today:
        raise ValueError("The investment start date cannot be in the future.")
    if any(value < 0 for value in amounts.values()):
        raise ValueError("Invested amounts cannot be negative.")
    text = extract_pdf_text(pdf)
    parsed = {key: parse_holdings(text, fund) for key, fund in FUNDS.items()}
    nlb, yahoo = NlbClient(), YahooClient(ROOT / "ticker_cache.json")
    reports = []
    for key, fund in FUNDS.items():
        holdings_date, holdings = parsed[key]
        start = min(today - timedelta(days=400), holdings_date - timedelta(days=7), since - timedelta(days=7))
        nav = nlb.get_nav_history(fund, start, today)
        result = analyze_fund(
            fund, holdings_date, holdings, nav, yahoo, today, 90.0, 6,
            amounts.get(key, 0.0), live, since,
        )
        official_date = date.fromisoformat(result["metrics"]["latest_date"])
        result["nav_history"] = [
            {"date": point.day.isoformat(), "nav": point.nav}
            for point in nav if point.day <= official_date
        ]
        reports.append(result)
    yahoo.save_cache()
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": today.isoformat(), "source_pdf": pdf.name,
        "return_since": since.isoformat(), "market_mode": "live" if live else "official-close",
        "investment_summary": build_investment_summary(reports), "funds": reports,
    }
    write_json(REPORT_PATH, report)
    return report
