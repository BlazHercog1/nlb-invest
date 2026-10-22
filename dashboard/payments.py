"""Monthly progress and payment controls for the local dashboard."""
from datetime import date
from uuid import uuid4

import streamlit as st

from dashboard import data
from nlb_invest.analytics import add_months
from nlb_invest.models import FUNDS
from nlb_invest.payments import monthly_story, story_months
from nlb_invest.reporting import fmt_eur


def render_monthly_story(report):
    months = story_months(report["funds"])
    if not months:
        return
    st.subheader("Your monthly story")
    month = st.selectbox(
        "Month", list(reversed(months)), key="story_month",
        format_func=lambda value: date.fromisoformat(value + "-01").strftime("%B %Y"),
    )
    story = monthly_story(report["funds"], month)
    columns = st.columns(3)
    columns[0].metric("Added this month", fmt_eur(story["added_eur"]))
    columns[1].metric("Investment gain / loss this month", fmt_eur(story["gain_eur"]))
    columns[2].metric("Balance change this month", fmt_eur(story["balance_change_eur"]))
    earnings = "earned" if story["gain_eur"] >= 0 else "lost"
    direction = "increased" if story["balance_change_eur"] >= 0 else "decreased"
    st.write(
        f"You added {fmt_eur(story['added_eur'])}. Your investments {earnings} "
        f"{fmt_eur(abs(story['gain_eur']))}. Your balance {direction} by "
        f"{fmt_eur(abs(story['balance_change_eur']))}."
    )
    st.caption(
        f"Through {story['through']}, using published NLB prices. "
        f"{story['estimated_payment_count']} estimated payment(s) are included in the balance. "
        "Contributions enter on their NAV date; gain/loss includes entered fees."
    )
    st.dataframe([{
        "Fund": FUNDS[row["key"]].title,
        "Opening value (EUR)": row["opening_value_eur"],
        "Added (EUR)": row["added_eur"],
        "Investment gain/loss (EUR)": row["gain_eur"],
        "Closing value (EUR)": row["closing_value_eur"],
    } for row in story["funds"]], hide_index=True, width="stretch",
        column_config={name: st.column_config.NumberColumn(format="%.2f") for name in (
            "Opening value (EUR)", "Added (EUR)", "Investment gain/loss (EUR)", "Closing value (EUR)",
        )})


def saved_payments(plan):
    """Give older saved reports the same editable IDs as a fresh report."""
    rows = []
    for item in plan.get("payments", plan.get("contributions", [])):
        row = dict(item)
        row.setdefault("id", f"{row['type']}:{row['scheduled_date']}")
        row.setdefault("status", "estimated")
        row.setdefault("settlement", "included")
        rows.append(row)
    return rows


def payment_input(plan, item):
    """Never turn calculated units into user-entered units on confirmation."""
    fields = ("id", "type", "scheduled_date", "status", "amount_eur", "actual_date", "fee_eur")
    record = {key: item[key] for key in fields if key in item}
    original = next((p for p in plan.get("payment_records", []) if p["id"] == item["id"]), {})
    record.update(original)
    if item.get("units_source") == "entered":
        record["units"] = item["units"]
    return record


def render_payment_history(fund):
    plan = fund["investment_plan"]
    latest_nav = fund["metrics"]["latest_nav_eur"]
    rows = []
    for item in saved_payments(plan):
        included = item["settlement"] == "included"
        value = item.get("official_value_eur")
        if included and value is None:
            value = item["units"] * latest_nav
        gain = value - item["amount_eur"] if value is not None else None
        nav_day = item.get("nav_date_used")
        status = item["status"].title()
        if item["settlement"] == "pending":
            status += " (pending NAV)"
        rows.append({
            "Type": item["type"].title(), "Status": status,
            "Scheduled date": item["scheduled_date"],
            "Purchase date": item.get("actual_date") or item["scheduled_date"],
            "NAV date used": nav_day, "Amount (EUR)": item["amount_eur"],
            "Fee (EUR)": item.get("fee_eur", 0.0), "NAV (EUR)": item.get("nav_eur"),
            "Units": item.get("units"),
            "Units source": item.get("units_source", "estimated").title() if included else None,
            "Current value (EUR)": value, "Gain/loss (EUR)": gain,
            "Return (%)": gain / item["amount_eur"] * 100 if gain is not None else None,
            "15-year holding date": (
                item.get("fifteen_year_holding_date")
                or add_months(date.fromisoformat(nav_day), 180).isoformat()
            ) if nav_day else None,
        })
    st.markdown("#### Transaction history")
    st.caption(
        f"Current values use NLB's published price on {fund['metrics']['latest_date']}. "
        "Estimated payments assume the plan was followed. Skipped and pending payments have no value yet."
    )
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        **{name: st.column_config.NumberColumn(format="%.2f") for name in (
            "Amount (EUR)", "Fee (EUR)", "Current value (EUR)", "Gain/loss (EUR)", "Return (%)",
        )},
        "NAV (EUR)": st.column_config.NumberColumn(format="%.4f"),
        "Units": st.column_config.NumberColumn(format="%.6f"),
    })
    st.caption(
        "The 15-year date is informational and follows NLB's published tax explanation; "
        "it is not a tax calculation."
    )


def _save(report, settings, fund_key, record=None, *, reset_id=None):
    try:
        updated = data.save_payment_change(report, settings, fund_key, record, reset_id=reset_id)
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(f"Could not save payment: {exc}")
        return
    st.session_state.report = updated
    st.session_state.payment_notice = "Payment saved. Balances and monthly progress have been updated using saved prices."
    st.rerun()


def render_payment_manager(report, settings, fund):
    if not fund.get("nav_history"):
        st.info("Refresh once to load NLB price history before editing payments.")
        return
    fund_key = fund["key"]
    plan = fund["investment_plan"]
    rows = saved_payments(plan)
    with st.expander("Manage payments"):
        st.caption(
            "Confirm a payment, correct its details, or skip it. Confirmation records that it happened. "
            "Enter units from your statement to match your account; otherwise units are estimated "
            "using the first published NLB price on or after the purchase date. Amount paid includes the fee."
        )
        if rows:
            by_id = {p["id"]: p for p in rows}
            selected = st.selectbox(
                "Payment", list(by_id), index=len(by_id) - 1, key="payment_" + fund_key,
                format_func=lambda identifier: (
                    f"{by_id[identifier]['scheduled_date']} - {by_id[identifier]['type'].title()} - "
                    f"{fmt_eur(by_id[identifier]['amount_eur'])} - {by_id[identifier]['status'].title()}"
                ),
            )
            item = by_id[selected]
            record = payment_input(plan, item)
            buttons = st.columns(3)
            if buttons[0].button("Confirm payment", key="confirm_" + fund_key):
                _save(report, settings, fund_key, {**record, "status": "confirmed",
                      "actual_date": record.get("actual_date") or record["scheduled_date"]})
            if buttons[1].button("Skip payment", key="skip_" + fund_key):
                _save(report, settings, fund_key, {**record, "status": "skipped"})
            reset_label = "Remove extra payment" if item["type"] == "extra" else "Restore estimate"
            if buttons[2].button(reset_label, key="reset_" + fund_key):
                _save(report, settings, fund_key, reset_id=selected)

            revision = report.get("payments_updated_at", report["generated_at"])
            widget_key = f"{fund_key}_{selected}_{item['status']}_{revision}"
            with st.form("edit_payment_" + widget_key):
                amount = st.number_input(
                    "Amount paid (EUR)", min_value=0.01, value=float(record["amount_eur"]),
                    step=10.0, format="%.2f", key="payment_amount_" + widget_key,
                )
                purchase_date = st.date_input(
                    "Purchase date", value=min(date.fromisoformat(
                        record.get("actual_date") or record["scheduled_date"]), date.today()),
                    max_value=date.today(), key="payment_date_" + widget_key,
                )
                fee = st.number_input(
                    "Fee included in amount paid (EUR)", min_value=0.0,
                    value=float(record.get("fee_eur", 0.0)), step=0.1, format="%.2f",
                    key="payment_fee_" + widget_key,
                )
                units = st.number_input(
                    "Units received (optional)", min_value=0.000001,
                    value=record.get("units"), step=0.001, format="%.6f",
                    key="payment_units_" + widget_key,
                )
                status = st.selectbox(
                    "Payment status", ["Estimated", "Confirmed", "Skipped"],
                    index=["estimated", "confirmed", "skipped"].index(record["status"]),
                    key="payment_status_" + widget_key,
                )
                if st.form_submit_button("Save payment"):
                    _save(report, settings, fund_key, {
                        **record, "amount_eur": amount, "actual_date": purchase_date.isoformat(),
                        "fee_eur": fee, "units": units, "status": status.lower(),
                    })
        with st.form("extra_payment_" + fund_key, clear_on_submit=True):
            st.markdown("**Add an extra payment**")
            extra_amount = st.number_input(
                "Extra amount paid (EUR)", min_value=0.01, value=50.0,
                step=10.0, format="%.2f", key="extra_amount_" + fund_key,
            )
            extra_date = st.date_input(
                "Extra payment purchase date", value=date.today(), max_value=date.today(),
                key="extra_date_" + fund_key,
            )
            extra_fee = st.number_input(
                "Extra payment fee (EUR)", min_value=0.0, value=0.0,
                step=0.1, format="%.2f", key="extra_fee_" + fund_key,
            )
            extra_units = st.number_input(
                "Extra units received (optional)", min_value=0.000001, value=None,
                step=0.001, format="%.6f", key="extra_units_" + fund_key,
            )
            if st.form_submit_button("Add extra payment"):
                _save(report, settings, fund_key, {
                    "id": "extra:" + uuid4().hex, "type": "extra", "status": "confirmed",
                    "scheduled_date": extra_date.isoformat(), "actual_date": extra_date.isoformat(),
                    "amount_eur": extra_amount, "fee_eur": extra_fee, "units": extra_units,
                })
