"""Run with: py -3 -m streamlit run dashboard/app.py"""
from datetime import date

import plotly.graph_objects as go
import streamlit as st

from dashboard.data import (
    DEFAULT_DATE, LOCAL, ROOT, SETTINGS_PATH, load_report, load_settings,
    prefer_newer_report, recurring_refresh_needed, refresh_recurring_report,
    refresh_report, report_exports, show_due_payments, write_json,
)
from nlb_invest.analytics import add_months
from dashboard.payments import render_monthly_story, render_payment_history, render_payment_manager
from nlb_invest.models import FUNDS
from nlb_invest.payments import monthly_schedule
from nlb_invest.reporting import fmt_eur, fmt_pct_value


def saved_date(value, fallback):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def performance_chart(funds):
    figure = go.Figure()
    colors = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A"]
    for index, fund in enumerate(funds):
        analysis = fund["return_since_date"]
        anchor_date = analysis["nav_date_used"]
        anchor_nav = analysis["starting_nav_eur"]
        points = [p for p in fund.get("nav_history", []) if p["date"] >= anchor_date]
        if points:
            title = FUNDS[fund["key"]].title
            color = colors[index % len(colors)]
            official_returns = [(p["nav"] / anchor_nav - 1) * 100 for p in points]
            figure.add_trace(go.Scatter(
                x=[p["date"] for p in points],
                y=official_returns,
                customdata=[p["nav"] for p in points],
                name=title, mode="lines",
                line=dict(color=color), legendgroup=fund["key"],
                hovertemplate=(
                    "%{x}<br>Official NAV: EUR %{customdata:,.4f}"
                    "<br>Return: %{y:+.2f}%<extra>%{fullData.name}</extra>"
                ),
            ))
    figure.update_layout(
        yaxis_title="Official return from starting NAV (%)", xaxis_title=None,
        legend=dict(orientation="h", y=-0.25), margin=dict(l=10, r=10, t=10, b=10),
        height=370,
    )
    return figure


def has_live_estimate(fund):
    return (
        fund.get("market_mode") == "live"
        and fund.get("estimated_live_nav_eur") is not None
        and bool(fund.get("market_time_latest"))
    )


def live_performance_chart(funds):
    colors = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A"]
    live_funds = [fund for fund in funds if has_live_estimate(fund)]
    figure = go.Figure()
    if live_funds:
        returns = [
            fund["return_since_date"]["estimated_live_return_pct"]
            for fund in live_funds
        ]
        figure.add_trace(go.Bar(
            x=[FUNDS[fund["key"]].title for fund in live_funds],
            y=returns,
            customdata=[
                [fund["estimated_live_nav_eur"], fund["market_time_latest"]]
                for fund in live_funds
            ],
            marker_color=colors[:len(live_funds)],
            text=[f"{value:+.2f}%" for value in returns], textposition="outside",
            hovertemplate=(
                "%{x}<br>Estimated live NAV: EUR %{customdata[0]:,.4f}"
                "<br>Estimated return: %{y:+.2f}%"
                "<br>Latest quote: %{customdata[1]}<extra></extra>"
            ),
        ))
    figure.update_layout(
        yaxis_title="Estimated live return from starting NAV (%)", xaxis_title=None,
        margin=dict(l=10, r=10, t=10, b=10), height=370, showlegend=False,
    )
    return figure


def investment_snapshot(fund, live):
    if live and not has_live_estimate(fund):
        return None
    analysis = fund["return_since_date"]
    plan = fund.get("investment_plan")
    if plan:
        contributed = plan["total_contributed_eur"]
        if live:
            value = plan["estimated_current_value_eur"]
        else:
            value = plan.get("official_value_eur")
            if value is None and plan.get("value_history"):
                value = plan["value_history"][-1]["value_eur"]
    else:
        contributed = fund.get("invested_eur") or 0.0
        if not contributed:
            return None
        return_pct = (
            analysis["estimated_live_return_pct"]
            if live else analysis["official_return_pct"]
        )
        value = contributed * (1.0 + return_pct / 100.0)
    if value is None:
        return None
    gain = value - contributed
    return {
        "contributed_eur": contributed,
        "value_eur": value,
        "gain_eur": gain,
        "return_pct": gain / contributed * 100.0 if contributed else None,
    }


def portfolio_snapshot(funds, live):
    snapshots = [investment_snapshot(fund, live) for fund in funds]
    snapshots = [snapshot for snapshot in snapshots if snapshot]
    contributed = sum(snapshot["contributed_eur"] for snapshot in snapshots)
    value = sum(snapshot["value_eur"] for snapshot in snapshots)
    gain = value - contributed
    return {
        "contributed_eur": contributed,
        "value_eur": value,
        "gain_eur": gain,
        "return_pct": gain / contributed * 100.0 if contributed else None,
    }


def contribution_chart(plan):
    history = plan.get("value_history", [])
    figure = go.Figure()
    if history:
        figure.add_trace(go.Scatter(
            x=[point["date"] for point in history],
            y=[point["contributed_eur"] for point in history],
            name="Total contributed", mode="lines", line_shape="hv",
            hovertemplate="%{x}<br>EUR %{y:,.2f}<extra>%{fullData.name}</extra>",
        ))
        figure.add_trace(go.Scatter(
            x=[point["date"] for point in history],
            y=[point["value_eur"] for point in history],
            name="Value at official NAV", mode="lines",
            hovertemplate="%{x}<br>EUR %{y:,.2f}<extra>%{fullData.name}</extra>",
        ))
    figure.update_layout(
        yaxis_title="EUR", xaxis_title=None,
        legend=dict(orientation="h", y=-0.25), margin=dict(l=10, r=10, t=10, b=10),
        height=370,
    )
    return figure


def main():
    st.set_page_config(page_title="My NLB investments", page_icon="📈", layout="wide")
    st.title("My NLB investments")
    st.caption("Your three funds, in one place.")
    if notice := st.session_state.pop("payment_notice", None):
        st.success(notice)
    st.session_state.report = prefer_newer_report(
        st.session_state.get("report"), load_report(),
    )
    settings = load_settings()

    with st.sidebar:
        st.header("My investments")
        st.caption(
            "For each fund, use either a one-time initial investment of EUR 1,000 "
            "or more, a savings plan of EUR 40 to EUR 500 per payment, or both."
        )
        with st.form("investment_settings"):
            legacy_date = saved_date(settings.get("return_since"), DEFAULT_DATE)
            saved_plans = settings.get("plans", {})
            plans = {}
            for key, fund in FUNDS.items():
                saved_plan = saved_plans.get(key, {})
                default_initial_date = saved_date(saved_plan.get("initial_date"), legacy_date)
                default_monthly_start = saved_date(
                    saved_plan.get("monthly_start_date"), add_months(default_initial_date),
                )
                saved_changes = saved_plan.get("monthly_changes") or []
                saved_change = saved_changes[0] if saved_changes else {}
                default_change_date = saved_date(
                    saved_change.get("effective_date"), add_months(default_monthly_start),
                )
                with st.expander(fund.title, expanded=bool(
                    saved_plan.get("initial_amount_eur")
                    or saved_plan.get("monthly_amount_eur")
                    or settings.get("amounts", {}).get(key, 0.0)
                )):
                    initial_amount = st.number_input(
                        "One-time initial investment (EUR; 0 or at least 1,000)",
                        min_value=0.0,
                        value=float(saved_plan.get(
                            "initial_amount_eur", settings.get("amounts", {}).get(key, 0.0),
                        )),
                        step=100.0, format="%.2f", key="amount_" + key,
                    )
                    initial_date = st.date_input(
                        "Initial investment date", value=min(default_initial_date, date.today()),
                        max_value=date.today(), key="initial_date_" + key,
                    )
                    monthly_amount = st.number_input(
                        "Savings-plan contribution (EUR; 0 or 40-500)", min_value=0.0,
                        max_value=500.0, value=float(saved_plan.get("monthly_amount_eur", 0.0)),
                        step=10.0, format="%.2f", key="monthly_amount_" + key,
                    )
                    monthly_start = st.date_input(
                        "First payment date (repeats every month)", value=default_monthly_start,
                        key="monthly_start_" + key,
                    )
                    if monthly_amount:
                        st.caption(
                            f"Invests EUR {monthly_amount:,.2f} every month on day "
                            f"{monthly_start.day}, starting {monthly_start.isoformat()}."
                        )
                    changed_monthly_amount = st.number_input(
                        "Changed monthly amount (EUR; 0 for no change)", min_value=0.0,
                        max_value=500.0, value=float(saved_change.get("amount_eur", 0.0)),
                        step=10.0, format="%.2f", key="changed_monthly_amount_" + key,
                    )
                    change_date = st.date_input(
                        "New amount effective from", value=default_change_date,
                        key="monthly_change_date_" + key,
                    )
                    st.caption(
                        "The one-time investment is optional when a savings plan is set. "
                        "Use 0 for the changed amount when the regular payment has not changed."
                    )
                plans[key] = {
                    "initial_amount_eur": initial_amount,
                    "initial_date": initial_date,
                    "monthly_amount_eur": monthly_amount,
                    "payments": saved_plan.get("payments", []),
                    "monthly_start_date": monthly_start,
                    "monthly_changes": ([{
                        "effective_date": change_date,
                        "amount_eur": changed_monthly_amount,
                    }] if changed_monthly_amount else []),
                }
            amounts = {key: plan["initial_amount_eur"] for key, plan in plans.items()}
            active_dates = [
                contribution_date
                for plan in plans.values()
                for contribution_date in (
                    [plan["initial_date"]] if plan["initial_amount_eur"] else []
                ) + (
                    [plan["monthly_start_date"]] if plan["monthly_amount_eur"] else []
                )
            ]
            since = min(active_dates, default=min(legacy_date, date.today()))
            pdfs = sorted([*ROOT.glob("*.pdf"), *LOCAL.glob("*.pdf")])
            saved_pdf_names = settings.get("pdfs")
            default_pdfs = (
                [path for path in pdfs if path.name in saved_pdf_names]
                if saved_pdf_names is not None else pdfs
            )
            chosen = st.multiselect(
                "Monthly holdings reports", pdfs, default=default_pdfs,
                format_func=lambda p: p.name,
            )
            uploaded = st.file_uploader(
                "Or upload new NLB reports", type=["pdf"], accept_multiple_files=True,
            )
            live = st.checkbox("Include latest market estimate", value=settings.get("live", True))
            submitted = st.form_submit_button("Refresh", type="primary", width="stretch")
        with st.expander("Official NLB savings-plan rules"):
            st.markdown(
                """
- Regular savings-plan payments are **EUR 40-500**; minors have no stated upper limit.
- The payment frequency is flexible. Monthly standing orders are recommended, not required.
- Published entry-fee discounts are 15% for EUR 40-59.99, 33.33% for
  EUR 60-149.99, and 50% for EUR 150-500. Minors pay no entry fee until age 18.
- Withdrawals may be requested at any time; NLB states payment is made within
  six business days.
- Under the currently published tax rules, FIFO applies and every payment has
  its own holding period; the published rate reaches 0% after 15 years.

[Official savings-plan details](https://www.nlbskladi.si/nalozbene-moznosti/nacini-varcevanja/postopno-varcevanje-z-varcevalnim-nacrtom)
                """
            )
            st.caption(
                "Checked 17 September 2026. This tracker does not deduct entry fees, "
                "model withdrawals, or calculate tax."
            )
        st.caption("Settings and reports stay on this computer.")

    if submitted:
        report_pdfs = list(chosen)
        refresh_progress = None
        try:
            if uploaded:
                LOCAL.mkdir(parents=True, exist_ok=True)
                for upload in uploaded:
                    uploaded_pdf = LOCAL / Path(upload.name).name
                    uploaded_pdf.write_bytes(upload.getvalue())
                    report_pdfs.append(uploaded_pdf)
            if not report_pdfs:
                st.error("Choose or upload at least one NLB monthly report first.")
            else:
                refresh_progress = st.progress(0.0, text="Starting refresh...")
                with st.spinner("Refreshing your funds. This may take a few minutes..."):
                    report = refresh_report(
                        report_pdfs, since, amounts, live, plans,
                        on_progress=lambda value, message: refresh_progress.progress(
                            value * 0.95, text=message,
                        ),
                    )
                write_json(SETTINGS_PATH, {
                    "return_since": since.isoformat(), "amounts": amounts,
                    "plans": {
                        key: {
                            "initial_amount_eur": plan["initial_amount_eur"],
                            "initial_date": plan["initial_date"].isoformat(),
                            "monthly_amount_eur": plan["monthly_amount_eur"],
                            "payments": plan.get("payments", []),
                            "monthly_start_date": plan["monthly_start_date"].isoformat(),
                            "monthly_changes": [
                                {
                                    "effective_date": change["effective_date"].isoformat(),
                                    "amount_eur": change["amount_eur"],
                                }
                                for change in plan["monthly_changes"]
                            ],
                        }
                        for key, plan in plans.items()
                    },
                    "pdf": report_pdfs[-1].name,
                    "pdfs": [path.name for path in report_pdfs], "live": live,
                })
                st.session_state.report = report
                refresh_progress.progress(1.0, text="Refresh complete.")
                st.success("Updated.")
        except Exception as exc:
            if refresh_progress is not None:
                refresh_progress.empty()
            st.error(f"Could not refresh: {exc}")
            st.info("Your previous report is still shown below. Check the PDF and your internet connection, then retry.")

    if not submitted and recurring_refresh_needed(st.session_state.report, settings):
        previous = st.session_state.report
        attempt = (previous["generated_at"], date.today().isoformat())
        if st.session_state.get("recurring_refresh_attempt") != attempt:
            st.session_state.recurring_refresh_attempt = attempt
            progress = st.progress(0.0, text="Including your latest monthly payment...")
            try:
                with st.spinner("Updating recurring payments and their fund prices..."):
                    st.session_state.report = refresh_recurring_report(
                        previous, settings,
                        on_progress=lambda value, message: progress.progress(value, text=message),
                    )
                st.session_state.pop("recurring_refresh_error", None)
                st.success("Your monthly payments are up to date.")
            except (ValueError, RuntimeError, OSError) as exc:
                st.session_state.report = show_due_payments(previous, settings)
                st.session_state.recurring_refresh_error = str(exc)
            finally:
                progress.empty()
    if submitted:
        st.session_state.pop("recurring_refresh_error", None)
    elif error := st.session_state.get("recurring_refresh_error"):
        st.warning(
            f"Could not fetch prices for the latest monthly payment: {error}. "
            "Due payments are shown as pending NAV. Click Refresh to retry."
        )

    report = st.session_state.report
    if not report:
        st.info("Enter your invested amounts, choose a monthly report, and click Refresh to get started.")
        return

    source_pdfs = report.get("source_pdfs") or [report["source_pdf"]]
    st.caption(
        f"Last refreshed: {report['generated_at']} · "
        f"Reports ({len(source_pdfs)}): {', '.join(source_pdfs)}"
    )
    if date.fromisoformat(report["as_of"]) < date.today():
        st.info("These are saved results from an earlier day. Click Refresh for the latest available data.")
    st.caption("Prices come from the last successful refresh. Payment edits update the saved balances and downloads.")
    text_export, json_export = report_exports(report)
    export_columns = st.columns(2)
    with export_columns[0]:
        st.download_button(
            "Download text report", data=text_export,
            file_name="latest_report.txt", mime="text/plain",
            on_click="ignore", width="stretch",
        )
    with export_columns[1]:
        st.download_button(
            "Download JSON report", data=json_export,
            file_name="latest_report.json", mime="application/json",
            on_click="ignore", width="stretch",
        )
    unavailable = sum(bool(h["error"]) for f in report["funds"] for h in f["holdings"])
    if unavailable:
        st.warning(f"{unavailable} holdings have unavailable prices. Estimates use the successfully tracked holdings; see their status below.")

    has_plans = any(fund.get("investment_plan") for fund in report["funds"])
    st.header("Official NLB data")
    st.caption("Valued at published NLB unit prices. Scheduled payments remain estimates until confirmed.")
    official_summary = portfolio_snapshot(report["funds"], live=False)
    if official_summary["contributed_eur"] > 0:
        cols = st.columns(3)
        cols[0].metric(
            "Total contributed" if has_plans else "Invested",
            fmt_eur(official_summary["contributed_eur"]),
        )
        cols[1].metric("Official value", fmt_eur(official_summary["value_eur"]))
        cols[2].metric(
            "Official gain / loss", fmt_eur(official_summary["gain_eur"]),
            fmt_pct_value(official_summary["return_pct"]),
        )

    for column, fund in zip(st.columns(len(report["funds"]) or 1), report["funds"]):
        with column:
            st.subheader(FUNDS[fund["key"]].title)
            analysis = fund["return_since_date"]
            plan = fund.get("investment_plan")
            snapshot = investment_snapshot(fund, live=False)
            if snapshot:
                st.metric("Total contributed", fmt_eur(snapshot["contributed_eur"]))
                st.metric("Official value", fmt_eur(snapshot["value_eur"]))
                st.metric(
                    "Official gain / loss", fmt_eur(snapshot["gain_eur"]),
                    fmt_pct_value(snapshot["return_pct"]),
                )
            else:
                st.metric("Official return", fmt_pct_value(analysis["official_return_pct"]))
            if plan:
                monthly_text = (
                    f"EUR {plan['monthly_amount_eur']:,.2f} every month on day "
                    f"{date.fromisoformat(plan['monthly_start_date']).day} "
                    f"(first payment {plan['monthly_start_date']})"
                    if plan["monthly_amount_eur"]
                    else "no monthly contribution"
                )
                change_text = ""
                if plan.get("monthly_changes"):
                    change = plan["monthly_changes"][0]
                    change_text = (
                        f"; then EUR {change['amount_eur']:,.2f}/month from "
                        f"{change['effective_date']}"
                    )
                initial_text = (
                    f"EUR {plan['initial_amount_eur']:,.2f} on {plan['initial_date']}"
                    if plan["initial_amount_eur"]
                    else "none"
                )
                st.caption(
                    f"Initial: {initial_text}  \n"
                    f"Then: {monthly_text}{change_text}  \n"
                    f"Contributions included: {plan['contribution_count']} | "
                    f"Total paid: {fmt_eur(plan['total_contributed_eur'])}"
                )
                schedule = monthly_schedule(plan, date.today())
                if schedule:
                    included = [
                        p for p in plan["contributions"] if p["type"] == "monthly"
                    ]
                    latest = max((p["scheduled_date"] for p in included), default="none yet")
                    st.caption(
                        f"Latest monthly payment included: {latest}. "
                        f"Next scheduled payment: {schedule['next_date']} "
                        f"({fmt_eur(schedule['next_amount_eur'])})."
                    )
                    pending = [
                        p for p in plan.get("payments", [])
                        if p.get("settlement") == "pending" and p["type"] == "monthly"
                    ]
                    if pending:
                        due = max(pending, key=lambda p: p["scheduled_date"])
                        st.info(
                            f"Payment on {due['scheduled_date']} "
                            f"({fmt_eur(due['amount_eur'])}) is awaiting a published NLB price."
                        )
            st.caption(
                f"Official NAV: {fmt_eur(fund['metrics']['latest_nav_eur'])} · "
                f"{fund['metrics']['latest_date']}\n\n"
                f"Starting NAV date: {analysis['nav_date_used']}"
            )

    st.subheader("Official fund performance")
    chart = performance_chart(report["funds"])
    if chart.data:
        st.plotly_chart(chart, width="stretch")
        st.caption("Published NLB NAV history measured from each fund's starting NAV.")
    else:
        st.info("Click Refresh to load the official performance chart.")

    st.divider()
    render_monthly_story(report)
    st.divider()
    st.header("Live market estimates")
    st.caption(
        "Estimated from disclosed holdings and available market prices. These values "
        "are not published NLB NAVs."
    )
    live_summary = portfolio_snapshot(report["funds"], live=True)
    if live_summary["contributed_eur"] > 0:
        cols = st.columns(3)
        cols[0].metric(
            "Total contributed" if has_plans else "Invested",
            fmt_eur(live_summary["contributed_eur"]),
        )
        cols[1].metric("Estimated live value", fmt_eur(live_summary["value_eur"]))
        cols[2].metric(
            "Estimated live gain / loss", fmt_eur(live_summary["gain_eur"]),
            fmt_pct_value(live_summary["return_pct"]),
        )

    for column, fund in zip(st.columns(len(report["funds"]) or 1), report["funds"]):
        with column:
            st.subheader(FUNDS[fund["key"]].title)
            if not has_live_estimate(fund):
                st.info("Live estimate unavailable for this fund.")
                continue
            analysis = fund["return_since_date"]
            snapshot = investment_snapshot(fund, live=True)
            if snapshot:
                st.metric("Total contributed", fmt_eur(snapshot["contributed_eur"]))
                st.metric("Estimated live value", fmt_eur(snapshot["value_eur"]))
                st.metric(
                    "Estimated live gain / loss", fmt_eur(snapshot["gain_eur"]),
                    fmt_pct_value(snapshot["return_pct"]),
                )
            else:
                st.metric(
                    "Estimated live return",
                    fmt_pct_value(analysis["estimated_live_return_pct"]),
                )
            st.caption(
                f"Estimated live NAV: {fmt_eur(fund['estimated_live_nav_eur'])}  \n"
                f"Latest market quote: {fund.get('market_time_latest') or 'Unavailable'}"
            )

    st.subheader("Current live return estimates")
    live_chart = live_performance_chart(report["funds"])
    if live_chart.data:
        st.plotly_chart(live_chart, width="stretch")
        st.caption(
            "Current estimated return for each fund, measured from its starting NAV. "
            "A live historical series is not available; each refresh produces one snapshot."
        )
    else:
        st.info("Refresh with 'Include latest market estimate' enabled to load live estimates.")

    planned_funds = [fund for fund in report["funds"] if fund.get("investment_plan")]
    if planned_funds:
        st.subheader("My contributions")
        contribution_key = st.selectbox(
            "Contribution fund", [fund["key"] for fund in planned_funds],
            format_func=lambda key: FUNDS[key].title, key="contribution_fund",
        )
        contribution_fund = next(
            fund for fund in planned_funds if fund["key"] == contribution_key
        )
        plan = contribution_fund["investment_plan"]
        chart = contribution_chart(plan)
        if chart.data:
            st.plotly_chart(chart, width="stretch")
        st.caption(
            "The chart uses published NLB NAV values. Scheduled payments without a published "
            "NAV yet are not included."
        )
        render_payment_history(contribution_fund)
        render_payment_manager(report, settings, contribution_fund)

    st.subheader("Holdings")
    keys = [fund["key"] for fund in report["funds"]]
    selected = st.selectbox("Fund", keys, format_func=lambda key: FUNDS[key].title)
    fund = next(f for f in report["funds"] if f["key"] == selected)
    snapshot_count = fund.get("holdings_snapshot_count", 1)
    st.caption(
        f"Latest holdings disclosed on {fund['holdings_date']} · "
        f"Monthly snapshots used: {snapshot_count} · "
        f"Successfully tracked: {fund['tracked_share_of_disclosed_pct']:.1f}% of disclosed equities · "
        f"Quote times: {fund.get('market_time_earliest') or 'Unavailable'} to "
        f"{fund.get('market_time_latest') or 'Unavailable'}"
    )
    rows = [{
        "Company": h["issuer"], "Symbol": h["symbol"],
        "Fund weight (%)": h["weight_pct"], "Return in EUR (%)": h["return_pct_eur"],
        "Contribution (pp)": h["contribution_pct"], "Quote time": h["market_time"],
        "Status": h["error"] or "Available",
    } for h in fund["holdings"]]
    st.dataframe(rows, width="stretch", hide_index=True)
    holding_periods = fund.get("holding_periods", [])
    if len(holding_periods) > 1:
        st.markdown("#### Monthly allocation periods")
        st.dataframe([{
            "Report": period.get("source_pdf", ""),
            "Allocation from": period["holdings_date"],
            "Used through": period["end_date"],
            "Equity contribution (pp)": period["estimated_equity_contribution_pct"],
            "Tracked share (%)": period["tracked_share_of_disclosed_pct"],
        } for period in holding_periods], width="stretch", hide_index=True)
    st.caption(
        "Each monthly holdings report is used until the next report takes over. "
        "The table above shows the latest disclosed holdings."
    )
    st.caption(
        "Live values are estimates based on monthly equity weights and available quotes. "
        "Untracked assets, including direct bonds, are assumed unchanged. Official NLB NAV determines actual fund performance."
    )


if __name__ == "__main__":
    main()
