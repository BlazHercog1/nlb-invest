"""Payment records and monthly summaries shared by the CLI and dashboard."""
from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any


def payment_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def finite_amount(value: Any, label: str, *, positive: bool = False) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(amount) or amount < 0 or (positive and amount == 0):
        raise ValueError(f"{label} must be a finite {'positive' if positive else 'non-negative'} number.")
    return amount


def normalize_payments(records: list[dict] | None) -> list[dict]:
    if records is None:
        return []
    if not isinstance(records, list):
        raise ValueError("Payment records must be a list.")
    result, seen = [], set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("Each payment record must be an object.")
        item = dict(raw)
        identifier = item.get("id", "")
        if not isinstance(identifier, str):
            raise ValueError("Payment ID must be text.")
        parts = identifier.split(":", 1)
        kind = item.get("type", parts[0])
        if kind not in ("initial", "monthly", "extra"):
            raise ValueError("Payment type must be initial, monthly or extra.")
        scheduled = payment_date(item.get("scheduled_date") or (parts[1] if len(parts) == 2 else ""))
        if kind != "extra" and identifier != f"{kind}:{scheduled.isoformat()}":
            raise ValueError("Scheduled payment ID must match its type and scheduled date.")
        if kind == "extra" and (not identifier.startswith("extra:") or len(identifier) <= 6):
            raise ValueError("An extra payment needs a unique extra: ID.")
        if identifier in seen:
            raise ValueError("Duplicate payment ID.")
        seen.add(identifier)
        status = item.get("status", "estimated")
        if status not in ("estimated", "confirmed", "skipped"):
            raise ValueError("Payment status must be estimated, confirmed or skipped.")
        item.update(id=identifier, type=kind, scheduled_date=scheduled.isoformat(), status=status)
        if item.get("amount_eur") is not None:
            item["amount_eur"] = finite_amount(item["amount_eur"], "Payment amount", positive=True)
        elif kind == "extra":
            raise ValueError("An extra payment needs an amount.")
        item["fee_eur"] = finite_amount(item.get("fee_eur") or 0, "Payment fee")
        if item.get("units") is not None:
            item["units"] = finite_amount(item["units"], "Received units", positive=True)
        if item.get("actual_date"):
            item["actual_date"] = payment_date(item["actual_date"]).isoformat()
        if item.get("amount_eur") is not None and item["fee_eur"] >= item["amount_eur"]:
            raise ValueError("Payment fee must be less than the amount paid.")
        # Persist inputs only; calculated values are rebuilt from NLB history.
        result.append({key: item[key] for key in (
            "id", "type", "scheduled_date", "status", "amount_eur",
            "actual_date", "fee_eur", "units",
        ) if key in item})
    return result


def normalize_plan(raw: dict) -> dict:
    plan = dict(raw)
    plan["payments"] = normalize_payments(plan.get("payments"))
    fallback = (
        plan.get("monthly_start_date")
        or next((p["scheduled_date"] for p in plan["payments"]), None)
        or "2026-08-19"
    )
    plan["initial_date"] = payment_date(plan.get("initial_date") or fallback)
    plan["monthly_start_date"] = (
        payment_date(plan["monthly_start_date"]) if plan.get("monthly_start_date") else None
    )
    for field in ("initial_amount_eur", "monthly_amount_eur"):
        plan[field] = finite_amount(plan.get(field) or 0, field)
    plan["monthly_changes"] = [
        {"effective_date": payment_date(change["effective_date"]),
         "amount_eur": finite_amount(change["amount_eur"], "Changed monthly amount")}
        for change in plan.get("monthly_changes", [])
    ]
    return plan


def has_payment_plan(plan: dict | None) -> bool:
    return bool(plan and (
        plan.get("initial_amount_eur") or plan.get("monthly_amount_eur") or plan.get("payments")
    ))


def plan_start(plan: dict, fallback: date) -> date:
    dates = []
    if plan.get("initial_amount_eur"):
        dates.append(payment_date(plan["initial_date"]))
    if plan.get("monthly_amount_eur"):
        dates.append(payment_date(plan["monthly_start_date"]))
    dates.extend(payment_date(p.get("actual_date") or p["scheduled_date"])
                 for p in plan.get("payments", []) if p.get("status") != "skipped")
    return min(dates, default=fallback)


def story_months(funds: list[dict]) -> list[str]:
    plans = [fund for fund in funds if fund.get("investment_plan")]
    histories = [fund["investment_plan"].get("value_history", []) for fund in plans]
    starts = [history[0]["date"] for history in histories if history]
    if not starts:
        return []
    first = payment_date(min(starts)).replace(day=1)
    last = min(payment_date(fund["metrics"]["latest_date"]) for fund in plans).replace(day=1)
    months = []
    while first <= last:
        months.append(first.strftime("%Y-%m"))
        first = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return months


def monthly_story(funds: list[dict], month: str) -> dict:
    """Reconcile balance changes with purchases, including fees in earnings."""
    first = date.fromisoformat(month + "-01")
    month_end = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    planned = [fund for fund in funds if fund.get("investment_plan")]
    through = min([month_end] + [payment_date(f["metrics"]["latest_date"]) for f in planned])
    if through < first:
        raise ValueError("No published NLB values are available for this month.")
    rows = []
    for fund in planned:
        plan = fund["investment_plan"]
        history = sorted(plan.get("value_history", []), key=lambda p: p["date"])
        before = [p for p in history if p["date"] < first.isoformat()]
        during = [p for p in history if p["date"] <= through.isoformat()]
        opening = before[-1] if before else {"value_eur": 0.0, "contributed_eur": 0.0}
        closing = during[-1] if during else {"value_eur": 0.0, "contributed_eur": 0.0}
        added = closing["contributed_eur"] - opening["contributed_eur"]
        change = closing["value_eur"] - opening["value_eur"]
        settled = [p for p in plan["contributions"] if p["nav_date_used"] <= through.isoformat()]
        rows.append({
            "key": fund["key"], "opening_value_eur": opening["value_eur"],
            "closing_value_eur": closing["value_eur"], "added_eur": added,
            "gain_eur": change - added, "balance_change_eur": change,
            "estimated_payment_count": sum(p.get("status", "estimated") == "estimated" for p in settled),
        })
    result = {"month": month, "through": through.isoformat(), "funds": rows}
    for field in ("opening_value_eur", "closing_value_eur", "added_eur",
                  "gain_eur", "balance_change_eur", "estimated_payment_count"):
        result[field] = sum(row[field] for row in rows)
    return result


def monthly_schedule(plan: dict, through: date) -> dict | None:
    """Describe a recurring calendar day, preserving it across short months."""
    if not plan.get("monthly_amount_eur") or not plan.get("monthly_start_date"):
        return None
    start = payment_date(plan["monthly_start_date"])
    changes = sorted(plan.get("monthly_changes", []),
                     key=lambda change: payment_date(change["effective_date"]))

    def occurrence(index):
        month_index = start.year * 12 + start.month - 1 + index
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        day = date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
        amount = float(plan["monthly_amount_eur"])
        for change in changes:
            if payment_date(change["effective_date"]) <= day:
                amount = float(change["amount_eur"])
        return day, amount

    elapsed = max(0, (through.year - start.year) * 12 + through.month - start.month)
    due, _ = occurrence(elapsed)
    if due > through:
        elapsed -= 1
    last_due, last_amount = occurrence(elapsed) if elapsed >= 0 else (None, None)
    next_due, next_amount = occurrence(elapsed + 1)
    return {
        "day": start.day, "first_date": start.isoformat(),
        "last_due_date": last_due.isoformat() if last_due else None,
        "last_due_amount_eur": last_amount,
        "next_date": next_due.isoformat(), "next_amount_eur": next_amount,
    }
