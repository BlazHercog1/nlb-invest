"""Local dashboard storage and an adapter to the existing tracker."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4

from nlb_invest.analytics import add_months, analyze_fund, calculate_contribution_plan
from nlb_invest.models import FUNDS, NavPoint
from nlb_invest.nlb_client import NlbClient
from nlb_invest.pdf_parser import extract_pdf_text, parse_holdings
from nlb_invest.payments import (
    has_payment_plan, normalize_plan, payment_date, plan_start,
)
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
    plans = {key: normalize_plan(plan) for key, plan in (investment_plans or {}).items()}
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
        plan_active = has_payment_plan(plan)
        if plan_active:
            analysis_start = plan_start(plan, since)
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
                plan.get("payments"), as_of=today,
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
            if any(has_payment_plan(plan) for plan in plans.values())
            else "lump-sum"
        ),
        "coverage_target_pct": 90.0,
        "investment_summary": build_investment_summary(reports), "funds": reports,
    }
    write_json(REPORT_PATH, report)
    write_text(TEXT_REPORT_PATH, render_text(report))
    notify(total_steps, "Report saved.")
    return report


def _commit_payment_files(contents: dict[Path, str]) -> None:
    """Stage every file before replacing any; restore previous files on failure."""
    previous = {path: path.read_bytes() if path.exists() else None for path in contents}
    staged, replaced = {}, []
    try:
        for path, content in contents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            staged[path] = temporary
            temporary.write_text(content, encoding="utf-8")
        for path, temporary in staged.items():
            temporary.replace(path)
            replaced.append(path)
    except OSError:
        for path in reversed(replaced):
            if previous[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous[path])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def save_payment_change(report: dict, settings: dict, fund_key: str,
                        record: dict | None = None, *, reset_id: str | None = None) -> dict:
    """Revalue one fund with cached prices; never fetch new market data."""
    updated = deepcopy(report)
    saved = deepcopy(settings)
    fund = next(f for f in updated["funds"] if f["key"] == fund_key)
    old_plan = fund.get("investment_plan")
    if not old_plan or not fund.get("nav_history"):
        raise ValueError("Refresh this fund once to load the NLB price history before editing payments.")
    configured = saved.get("plans", {}).get(fund_key)
    raw_plan = deepcopy(configured or {
        key: old_plan[key] for key in (
            "initial_amount_eur", "initial_date", "monthly_amount_eur",
            "monthly_start_date", "monthly_changes",
        ) if key in old_plan
    })
    records = deepcopy(
        configured.get("payments", old_plan.get("payment_records", []))
        if configured else old_plan.get("payment_records", [])
    )
    identifier = reset_id or (record or {}).get("id")
    if not identifier:
        raise ValueError("Choose a payment to update.")
    records = [item for item in records if item["id"] != identifier]
    if record is not None:
        records.append(record)
    raw_plan["payments"] = records
    plan = normalize_plan(raw_plan)
    nav = [NavPoint(date.fromisoformat(p["date"]), p["nav"], None, {})
           for p in fund["nav_history"]]
    earliest = plan_start(plan, date.fromisoformat(updated["return_since"]))
    if earliest < nav[0].day - timedelta(days=7):
        raise ValueError("This payment predates the saved NLB history. Use a date covered by the saved report.")
    if has_payment_plan(plan):
        investment = calculate_contribution_plan(
            nav, fund["estimated_live_nav_eur"],
            plan["initial_amount_eur"], plan["initial_date"],
            plan["monthly_amount_eur"], plan.get("monthly_start_date"),
            plan.get("monthly_changes"), plan.get("payments"),
            as_of=date.fromisoformat(updated["as_of"]),
        )
        fund["investment_plan"] = investment
        fund["invested_eur"] = investment["total_contributed_eur"]
        fund["current_value_eur"] = investment["estimated_current_value_eur"]
    else:
        fund.pop("investment_plan", None)
        fund["invested_eur"] = None
        fund["current_value_eur"] = None
    updated["investment_mode"] = (
        "contribution-plan" if any(f.get("investment_plan") for f in updated["funds"]) else "lump-sum"
    )
    updated["investment_summary"] = build_investment_summary(updated["funds"])
    updated["payments_updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    # Keep dates as JSON strings in saved settings.
    raw_plan["payments"] = plan["payments"]
    saved.setdefault("plans", {})[fund_key] = {
        **raw_plan,
        "initial_date": plan["initial_date"].isoformat(),
        "monthly_start_date": (
            plan["monthly_start_date"].isoformat() if plan["monthly_start_date"] else None
        ),
        "monthly_changes": [
            {"effective_date": change["effective_date"].isoformat(), "amount_eur": change["amount_eur"]}
            for change in plan["monthly_changes"]
        ],
    }
    _commit_payment_files({
        SETTINGS_PATH: json.dumps(saved, ensure_ascii=False, indent=2),
        REPORT_PATH: json.dumps(updated, ensure_ascii=False, indent=2),
        TEXT_REPORT_PATH: render_text(updated),
    })
    return updated


def prefer_newer_report(current: dict | None, saved: dict | None) -> dict | None:
    """Pick up scheduled tracker refreshes while a dashboard session is open."""
    def timestamp(report):
        if not report:
            return 0
        return max((datetime.fromisoformat(report[field]).timestamp()
                    for field in ("generated_at", "payments_updated_at") if report.get(field)),
                   default=0)
    return saved if timestamp(saved) > timestamp(current) else current or saved


def recurring_refresh_needed(report: dict | None, settings: dict, today: date | None = None) -> bool:
    """Fetch prices for due purchases once per day, preserving payment exceptions."""
    today = today or date.today()
    if not report or payment_date(report["as_of"]) >= today:
        return False
    for fund in report["funds"]:
        old_plan = fund.get("investment_plan") or {}
        plan = settings.get("plans", {}).get(fund["key"]) or old_plan
        if not plan.get("monthly_amount_eur") or not plan.get("monthly_start_date"):
            continue
        records = {p["id"]: p for p in plan.get("payments", old_plan.get("payment_records", []))
                   if "id" in p}
        rows = old_plan.get("payments", old_plan.get("contributions", []))
        known = {p.get("id", f"{p['type']}:{p['scheduled_date']}"): p for p in rows}
        start = payment_date(plan["monthly_start_date"])
        index, due = 0, start
        while due <= today:
            identifier = "monthly:" + due.isoformat()
            override = records.get(identifier, {})
            row = known.get(identifier, {})
            requested = payment_date(
                override.get("actual_date") or row.get("actual_date") or due
            )
            if (override.get("status", row.get("status")) != "skipped"
                    and requested <= today
                    and (identifier not in known or row.get("settlement") == "pending")):
                return True
            index += 1
            due = add_months(start, index)
    return False


def refresh_recurring_report(report: dict, settings: dict, *, on_progress=None) -> dict:
    """Use the saved plan and reports to catch up recurring purchases and prices."""
    names = settings.get("pdfs") or report.get("source_pdfs") or [report["source_pdf"]]
    pdfs = []
    for name in names:
        filename = Path(name).name
        path = next((candidate for candidate in (ROOT / filename, LOCAL / filename)
                     if candidate.is_file()), None)
        if path is None:
            raise ValueError(f"Monthly report is missing: {filename}. Choose it and click Refresh.")
        if path not in pdfs:
            pdfs.append(path)
    plans = settings.get("plans") or {
        fund["key"]: {
            **fund["investment_plan"],
            "payments": fund["investment_plan"].get("payment_records", []),
        }
        for fund in report["funds"] if fund.get("investment_plan")
    }
    return refresh_report(
        pdfs, payment_date(settings.get("return_since") or report["return_since"]),
        settings.get("amounts", {}), settings.get("live", report.get("market_mode") == "live"),
        plans, on_progress=on_progress,
    )


def show_due_payments(report: dict, settings: dict, today: date | None = None) -> dict:
    """Expose due but unpriced payments after a failed refresh, without inventing prices."""
    updated = deepcopy(report)
    today = today or date.today()
    for fund in updated["funds"]:
        old_plan = fund.get("investment_plan")
        if not old_plan or not fund.get("nav_history"):
            continue
        raw = settings.get("plans", {}).get(fund["key"]) or {
            **old_plan, "payments": old_plan.get("payment_records", []),
        }
        plan = normalize_plan(raw)
        if not has_payment_plan(plan):
            continue
        investment = calculate_contribution_plan(
            [NavPoint(payment_date(p["date"]), p["nav"], None, {}) for p in fund["nav_history"]],
            fund["estimated_live_nav_eur"],
            plan["initial_amount_eur"], plan["initial_date"],
            plan["monthly_amount_eur"], plan.get("monthly_start_date"),
            plan.get("monthly_changes"), plan.get("payments"), as_of=today,
        )
        fund["investment_plan"] = investment
        fund["invested_eur"] = investment["total_contributed_eur"]
        fund["current_value_eur"] = investment["estimated_current_value_eur"]
    updated["investment_summary"] = build_investment_summary(updated["funds"])
    updated["schedule_as_of"] = today.isoformat()
    return updated
