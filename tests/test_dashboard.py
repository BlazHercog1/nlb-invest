"""Verify dashboard calculations and failed-refresh preservation without network."""
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from dashboard import data
from nlb_invest.models import FUNDS, NavPoint
from nlb_invest.reporting import render_text


class DashboardDataTests(unittest.TestCase):
    def test_refresh_applies_initial_and_monthly_contributions_separately(self):
        nav = [
            NavPoint(date(2026, 7, 20), 10, None, {}),
            NavPoint(date(2026, 8, 20), 20, None, {}),
        ]
        plans = {
            "tech": {
                "initial_amount_eur": 1000,
                "initial_date": date(2026, 7, 20),
                "monthly_amount_eur": 100,
                "monthly_start_date": date(2026, 8, 20),
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            with patch.object(data, "REPORT_PATH", report_path), patch.object(data, "extract_pdf_text", return_value="pdf"), patch.object(data, "parse_holdings", return_value=(date(2026, 7, 31), [])), patch.object(data, "NlbClient") as nlb, patch.object(data, "YahooClient"):
                nlb.return_value.get_nav_history.return_value = nav
                report = data.refresh_report(
                    Path("report.pdf"), date(2026, 7, 20),
                    {key: 0 for key in FUNDS}, True, plans,
                )

        tech = next(fund for fund in report["funds"] if fund["key"] == "tech")
        self.assertEqual(tech["investment_plan"]["contribution_count"], 2)
        self.assertAlmostEqual(tech["invested_eur"], 1100)
        self.assertAlmostEqual(tech["investment_plan"]["accumulated_units"], 105)
        self.assertAlmostEqual(tech["current_value_eur"], 2100)
        self.assertAlmostEqual(report["investment_summary"]["estimated_gain_eur"], 1000)
        self.assertIn("Monthly contribution: EUR 100.00", render_text(report))

    def test_refresh_matches_tracker_and_keeps_official_history(self):
        nav = [
            NavPoint(date(2026, 8, 18), 100, None, {}),
            NavPoint(date(2026, 8, 20), 110, None, {}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            progress = []
            with patch.object(data, "REPORT_PATH", report_path), patch.object(data, "extract_pdf_text", return_value="pdf"), patch.object(data, "parse_holdings", return_value=(date(2026, 7, 31), [])), patch.object(data, "NlbClient") as nlb, patch.object(data, "YahooClient"):
                def get_nav(fund, start, end):
                    self.assertIn(fund.title, progress[-1][1])
                    self.assertIn("fetching official NLB values", progress[-1][1])
                    return nav

                nlb.return_value.get_nav_history.side_effect = get_nav
                report = data.refresh_report(
                    Path("report.pdf"), date(2026, 8, 19), {key: 1000 for key in FUNDS},
                    on_progress=lambda value, message: progress.append((value, message)),
                )
                self.assertAlmostEqual(report["investment_summary"]["estimated_current_value_eur"], 3300)
                self.assertAlmostEqual(report["investment_summary"]["estimated_gain_eur"], 300)
                self.assertEqual(report["funds"][0]["nav_history"][0]["date"], "2026-08-18")
                self.assertEqual(data.read_json(report_path), report)
                fractions = [value for value, _ in progress]
                self.assertEqual(fractions, sorted(fractions))
                self.assertEqual(fractions[0], 0.0)
                self.assertEqual(fractions[-1], 1.0)
                for fund in FUNDS.values():
                    self.assertTrue(any(fund.title in message and "holding prices" in message for _, message in progress))
                text_export, json_export = data.report_exports(report)
                self.assertEqual(text_export.decode("utf-8"), render_text(report))
                self.assertEqual(json.loads(json_export), report)
                self.assertIn(FUNDS["balanced"].title, json_export.decode("utf-8"))
                self.assertEqual(data.read_json(report_path), report)
                requested_start = nlb.return_value.get_nav_history.call_args.args[1]
                self.assertLessEqual(requested_start, date(2026, 8, 12))

    def test_failed_refresh_leaves_previous_report_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            data.write_json(report_path, {"previous": True})
            progress = []
            with patch.object(data, "REPORT_PATH", report_path), patch.object(data, "extract_pdf_text", side_effect=RuntimeError("Invalid PDF")):
                with self.assertRaisesRegex(RuntimeError, "Invalid PDF"):
                    data.refresh_report(
                        Path("bad.pdf"), date(2026, 8, 19), {},
                        on_progress=lambda value, message: progress.append((value, message)),
                    )
            self.assertEqual(data.read_json(report_path), {"previous": True})
            self.assertTrue(progress)
            self.assertLess(max(value for value, _ in progress), 1.0)




@unittest.skipUnless(importlib.util.find_spec("streamlit") and importlib.util.find_spec("plotly"), "Optional dashboard dependencies are not installed")
class DashboardInterfaceTests(unittest.TestCase):
    def test_contribution_plan_shows_chart_and_transaction_history(self):
        from streamlit.testing.v1 import AppTest

        nav = [
            NavPoint(date(2026, 7, 20), 10, None, {}),
            NavPoint(date(2026, 8, 20), 20, None, {}),
        ]
        plan = {
            "initial_amount_eur": 1000,
            "initial_date": date(2026, 7, 20),
            "monthly_amount_eur": 100,
            "monthly_start_date": date(2026, 8, 20),
            "monthly_changes": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with patch.object(data, "REPORT_PATH", local / "report.json"), patch.object(data, "extract_pdf_text", return_value="pdf"), patch.object(data, "parse_holdings", return_value=(date(2026, 7, 31), [])), patch.object(data, "NlbClient") as nlb, patch.object(data, "YahooClient"):
                nlb.return_value.get_nav_history.return_value = nav
                report = data.refresh_report(
                    Path("report.pdf"), date(2026, 7, 20),
                    {key: 0 for key in FUNDS}, True, {"tech": plan},
                )
            settings = {
                "return_since": "2026-07-20",
                "amounts": {"tech": 1000},
                "plans": {"tech": {
                    **plan,
                    "initial_date": "2026-07-20",
                    "monthly_start_date": "2026-08-20",
                }},
            }
            (local / "report.pdf").write_bytes(b"test")
            with patch.object(data, "ROOT", local), patch.object(data, "LOCAL", local), patch.object(data, "SETTINGS_PATH", local / "settings.json"), patch.object(data, "load_report", return_value=report), patch.object(data, "load_settings", return_value=settings):
                app = AppTest.from_file(
                    str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py"),
                    default_timeout=30,
                ).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.get("plotly_chart")), 2)
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(app.dataframe[0].value.iloc[0]["Type"], "Initial")
        self.assertEqual(app.dataframe[0].value.iloc[1]["Amount (EUR)"], 100)

    def test_inputs_require_refresh_and_failed_refresh_keeps_results(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest

        nav = [
            NavPoint(date(2026, 8, 18), 100, None, {}),
            NavPoint(date(2026, 8, 20), 110, None, {}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with patch.object(data, "REPORT_PATH", local / "report.json"), patch.object(data, "extract_pdf_text", return_value="pdf"), patch.object(data, "parse_holdings", return_value=(date(2026, 7, 31), [])), patch.object(data, "NlbClient") as nlb, patch.object(data, "YahooClient"):
                nlb.return_value.get_nav_history.return_value = nav
                report = data.refresh_report(Path("report.pdf"), date(2026, 8, 19), {key: 1000 for key in FUNDS})
            settings = {"return_since": "2026-08-19", "amounts": {key: 1000 for key in FUNDS}}
            (local / "report.pdf").write_bytes(b"test")
            with patch.object(data, "ROOT", local), patch.object(data, "LOCAL", local), patch.object(data, "SETTINGS_PATH", local / "settings.json"), patch.object(data, "load_report", return_value=report), patch.object(data, "load_settings", return_value=settings), patch.object(data, "refresh_report") as refresh, patch("streamlit.download_button", wraps=st.download_button) as download:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py"), default_timeout=30).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.metric[0].value, "EUR 3,000.00")
                self.assertEqual(len(app.get("plotly_chart")), 1)
                self.assertEqual(
                    [call.args[0] for call in download.call_args_list[-2:]],
                    ["Download text report", "Download JSON report"],
                )
                self.assertEqual(download.call_args_list[-2].kwargs["data"].decode("utf-8"), render_text(report))
                self.assertEqual(json.loads(download.call_args_list[-1].kwargs["data"]), report)
                self.assertEqual(download.call_args_list[-1].kwargs["on_click"], "ignore")
                app.number_input[0].set_value(2000).run()
                refresh.assert_not_called()
                self.assertEqual(app.metric[0].value, "EUR 3,000.00")

                def fail_refresh(*args, on_progress):
                    on_progress(0.5, "Fetching holding prices...")
                    raise RuntimeError("Connection unavailable")

                refresh.side_effect = fail_refresh
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertIn("Connection unavailable", app.error[0].value)
                self.assertEqual(len(app.get("progress")), 0)
                self.assertEqual(json.loads(download.call_args_list[-1].kwargs["data"]), report)
                self.assertEqual(app.metric[0].value, "EUR 3,000.00")
                self.assertEqual(refresh.call_args.args[2]["tech"], 2000)

                def succeed_refresh(*args, on_progress):
                    on_progress(0.5, "Fetching holding prices...")
                    on_progress(1.0, "Report saved.")
                    return report

                refresh.side_effect = succeed_refresh
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(app.success)
                self.assertEqual(app.get("progress")[0].proto.value, 100)
                self.assertEqual(app.get("progress")[0].proto.text, "Refresh complete.")
                self.assertEqual(json.loads(download.call_args_list[-1].kwargs["data"]), report)
                self.assertEqual(data.read_json(local / "settings.json")["amounts"]["tech"], 2000)

if __name__ == "__main__":
    unittest.main()
