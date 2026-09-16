"""Run with: py -3 -m streamlit run dashboard/app.py"""
from datetime import date

import plotly.graph_objects as go
import streamlit as st

from dashboard.data import (
    DEFAULT_DATE, LOCAL, ROOT, SETTINGS_PATH, load_report, load_settings,
    refresh_report, report_exports, write_json,
)
from nlb_invest.analytics import add_months
from nlb_invest.models import FUNDS
from nlb_invest.reporting import fmt_eur, fmt_pct_value


def saved_date(value, fallback):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def performance_chart(funds):
    figure = go.Figure()
    for fund in funds:
        analysis = fund["return_since_date"]
        anchor_date = analysis["nav_date_used"]
        anchor_nav = analysis["starting_nav_eur"]
        points = [p for p in fund.get("nav_history", []) if p["date"] >= anchor_date]
        if points:
            figure.add_trace(go.Scatter(
                x=[p["date"] for p in points],
                y=[(p["nav"] / anchor_nav - 1) * 100 for p in points],
                name=FUNDS[fund["key"]].title, mode="lines",
                hovertemplate="%{x}<br>%{y:+.2f}%<extra>%{fullData.name}</extra>",
            ))
    figure.update_layout(
        yaxis_title="Official return (%)", xaxis_title=None,
        legend=dict(orientation="h", y=-0.25), margin=dict(l=10, r=10, t=10, b=10),
        height=370,
    )
    return figure


def main():
    st.set_page_config(page_title="My NLB investments", page_icon="📈", layout="wide")
    st.title("My NLB investments")
    st.caption("Your three funds, in one place.")
    if "report" not in st.session_state:
        st.session_state.report = load_report()
    settings = load_settings()

    with st.sidebar:
        st.header("My investments")
        st.caption(
            "For each fund, enter an initial investment of EUR 1,000 or more. "
            "A monthly contribution is optional and must be EUR 40 to EUR 400."
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
                with st.expander(fund.title, expanded=bool(
                    saved_plan.get("initial_amount_eur")
                    or settings.get("amounts", {}).get(key, 0.0)
                )):
                    initial_amount = st.number_input(
                        "Initial investment (EUR; 0 or at least 1,000)", min_value=0.0,
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
                        "Monthly contribution (EUR; 0 or 40-400)", min_value=0.0,
                        max_value=400.0, value=float(saved_plan.get("monthly_amount_eur", 0.0)),
                        step=10.0, format="%.2f", key="monthly_amount_" + key,
                    )
                    monthly_start = st.date_input(
                        "First monthly contribution date", value=default_monthly_start,
                        key="monthly_start_" + key,
                    )
                    st.caption("Enter 0 for the monthly contribution to disable it.")
                plans[key] = {
                    "initial_amount_eur": initial_amount,
                    "initial_date": initial_date,
                    "monthly_amount_eur": monthly_amount,
                    "monthly_start_date": monthly_start,
                }
            amounts = {key: plan["initial_amount_eur"] for key, plan in plans.items()}
            active_dates = [
                plan["initial_date"] for plan in plans.values()
                if plan["initial_amount_eur"] or plan["monthly_amount_eur"]
            ]
            since = min(active_dates, default=min(legacy_date, date.today()))
            pdfs = sorted([*ROOT.glob("*.pdf"), *LOCAL.glob("*.pdf")])
            chosen = st.selectbox(
                "Monthly holdings report", pdfs, format_func=lambda p: p.name,
                index=next((i for i, p in enumerate(pdfs) if p.name == settings.get("pdf")), 0) if pdfs else None,
            )
            uploaded = st.file_uploader("Or upload a new NLB report", type=["pdf"])
            live = st.checkbox("Include latest market estimate", value=settings.get("live", True))
            submitted = st.form_submit_button("Refresh", type="primary", width="stretch")
        st.caption("Settings and reports stay on this computer.")

    if submitted:
        pdf = chosen
        refresh_progress = None
        try:
            if uploaded is not None:
                LOCAL.mkdir(parents=True, exist_ok=True)
                pdf = LOCAL / "uploaded_holdings.pdf"
                pdf.write_bytes(uploaded.getvalue())
            if pdf is None:
                st.error("Choose or upload an NLB monthly report first.")
            else:
                refresh_progress = st.progress(0.0, text="Starting refresh...")
                with st.spinner("Refreshing your funds. This may take a few minutes..."):
                    report = refresh_report(
                        pdf, since, amounts, live, plans,
                        on_progress=lambda value, message: refresh_progress.progress(
                            value * 0.95, text=message,
                        ),
                    )
                write_json(SETTINGS_PATH, {
                    "return_since": since.isoformat(), "amounts": amounts,
                    "plans": {
                        key: {
                            **plan,
                            "initial_date": plan["initial_date"].isoformat(),
                            "monthly_start_date": plan["monthly_start_date"].isoformat(),
                        }
                        for key, plan in plans.items()
                    },
                    "pdf": pdf.name, "live": live,
                })
                st.session_state.report = report
                refresh_progress.progress(1.0, text="Refresh complete.")
                st.success("Updated.")
        except Exception as exc:
            if refresh_progress is not None:
                refresh_progress.empty()
            st.error(f"Could not refresh: {exc}")
            st.info("Your previous report is still shown below. Check the PDF and your internet connection, then retry.")

    report = st.session_state.report
    if not report:
        st.info("Enter your invested amounts, choose a monthly report, and click Refresh to get started.")
        return

    st.caption(f"Last refreshed: {report['generated_at']} · Report: {report['source_pdf']}")
    if date.fromisoformat(report["as_of"]) < date.today():
        st.info("These are saved results from an earlier day. Click Refresh for the latest available data.")
    st.caption("Results and downloads use the settings from the last successful refresh.")
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
    summary = report.get("investment_summary")
    if summary and summary["total_invested_eur"] > 0:
        has_plans = any(fund.get("investment_plan") for fund in report["funds"])
        cols = st.columns(3)
        cols[0].metric(
            "Total contributed" if has_plans else "Invested",
            fmt_eur(summary["total_invested_eur"]),
        )
        cols[1].metric("Estimated value", fmt_eur(summary["estimated_current_value_eur"]))
        cols[2].metric(
            "Estimated gain / loss", fmt_eur(summary["estimated_gain_eur"]),
            fmt_pct_value(summary["estimated_return_pct"]),
        )

    for column, fund in zip(st.columns(len(report["funds"]) or 1), report["funds"]):
        with column:
            st.subheader(FUNDS[fund["key"]].title)
            analysis = fund["return_since_date"]
            plan = fund.get("investment_plan")
            if plan:
                st.metric("Total contributed", fmt_eur(plan["total_contributed_eur"]))
                st.metric("Estimated value", fmt_eur(plan["estimated_current_value_eur"]))
                st.metric(
                    "Estimated gain / loss", fmt_eur(plan["estimated_gain_eur"]),
                    fmt_pct_value(plan["estimated_return_pct"]),
                )
                monthly_text = (
                    f"EUR {plan['monthly_amount_eur']:,.2f}/month from {plan['monthly_start_date']}"
                    if plan["monthly_amount_eur"]
                    else "no monthly contribution"
                )
                st.caption(
                    f"Initial: EUR {plan['initial_amount_eur']:,.2f} on {plan['initial_date']}  \n"
                    f"Then: {monthly_text}  \n"
                    f"Contributions included: {plan['contribution_count']} · "
                    f"Total paid: {fmt_eur(plan['total_contributed_eur'])}"
                )
            else:
                st.metric("Official return", fmt_pct_value(analysis["official_return_pct"]))
                st.metric("Estimated return", fmt_pct_value(analysis["estimated_live_return_pct"]))
                if (fund.get("invested_eur") or 0) > 0:
                    st.metric("Estimated value", fmt_eur(fund["current_value_eur"]))
            st.caption(
                f"Official NAV: {fmt_eur(fund['metrics']['latest_nav_eur'])} · "
                f"{fund['metrics']['latest_date']}\n\n"
                f"Starting NAV date: {analysis['nav_date_used']}"
            )

    st.subheader("Fund performance")
    chart = performance_chart(report["funds"])
    if chart.data:
        st.plotly_chart(chart, width="stretch")
        st.caption("Official NLB NAV history, measured from each fund's starting NAV. Live estimates are shown above.")
    else:
        st.info("Click Refresh to load the official performance chart.")

    st.subheader("Holdings")
    keys = [fund["key"] for fund in report["funds"]]
    selected = st.selectbox("Fund", keys, format_func=lambda key: FUNDS[key].title)
    fund = next(f for f in report["funds"] if f["key"] == selected)
    st.caption(
        f"Holdings disclosed on {fund['holdings_date']} · "
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
    st.caption("Holding returns and contributions are measured from the monthly report date.")
    st.caption(
        "Live values are estimates based on monthly equity weights and available quotes. "
        "Untracked assets, including direct bonds, are assumed unchanged. Official NLB NAV determines actual fund performance."
    )


if __name__ == "__main__":
    main()
