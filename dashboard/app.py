"""Run with: py -3 -m streamlit run dashboard/app.py"""
from datetime import date

import plotly.graph_objects as go
import streamlit as st

from dashboard.data import (
    DEFAULT_DATE, LOCAL, ROOT, SETTINGS_PATH, load_report, load_settings,
    refresh_report, write_json,
)
from nlb_invest.models import FUNDS
from nlb_invest.reporting import fmt_eur, fmt_pct_value


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
        st.caption("Each amount is treated as invested on the start date.")
        with st.form("investment_settings"):
            try:
                initial_date = date.fromisoformat(settings.get("return_since", DEFAULT_DATE.isoformat()))
            except (TypeError, ValueError):
                initial_date = DEFAULT_DATE
            since = st.date_input("Investment start date", value=min(initial_date, date.today()), max_value=date.today())
            amounts = {
                key: st.number_input(
                    fund.title + " (EUR)", min_value=0.0,
                    value=float(settings.get("amounts", {}).get(key, 0.0)),
                    step=100.0, format="%.2f", key="amount_" + key,
                ) for key, fund in FUNDS.items()
            }
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
        try:
            if uploaded is not None:
                LOCAL.mkdir(parents=True, exist_ok=True)
                pdf = LOCAL / "uploaded_holdings.pdf"
                pdf.write_bytes(uploaded.getvalue())
            if pdf is None:
                st.error("Choose or upload an NLB monthly report first.")
            else:
                with st.spinner("Updating NLB values and holding prices. This may take a few minutes…"):
                    report = refresh_report(pdf, since, amounts, live)
                write_json(SETTINGS_PATH, {
                    "return_since": since.isoformat(), "amounts": amounts,
                    "pdf": pdf.name, "live": live,
                })
                st.session_state.report = report
                st.success("Updated.")
        except Exception as exc:
            st.error(f"Could not refresh: {exc}")
            st.info("Your previous report is still shown below. Check the PDF and your internet connection, then retry.")

    report = st.session_state.report
    if not report:
        st.info("Enter your invested amounts, choose a monthly report, and click Refresh to get started.")
        return

    st.caption(
        f"Last refreshed: {report['generated_at']} · Investment start: {report['return_since']} · "
        f"Report: {report['source_pdf']}"
    )
    if date.fromisoformat(report["as_of"]) < date.today():
        st.info("These are saved results from an earlier day. Click Refresh for the latest available data.")
    st.caption("Results below use the settings from the last successful refresh.")
    unavailable = sum(bool(h["error"]) for f in report["funds"] for h in f["holdings"])
    if unavailable:
        st.warning(f"{unavailable} holdings have unavailable prices. Estimates use the successfully tracked holdings; see their status below.")
    summary = report.get("investment_summary")
    if summary and summary["total_invested_eur"] > 0:
        cols = st.columns(3)
        cols[0].metric("Invested", fmt_eur(summary["total_invested_eur"]))
        cols[1].metric("Estimated value", fmt_eur(summary["estimated_current_value_eur"]))
        cols[2].metric(
            "Estimated gain / loss", fmt_eur(summary["estimated_gain_eur"]),
            fmt_pct_value(summary["estimated_return_pct"]),
        )

    for column, fund in zip(st.columns(len(report["funds"]) or 1), report["funds"]):
        with column:
            st.subheader(FUNDS[fund["key"]].title)
            analysis = fund["return_since_date"]
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
