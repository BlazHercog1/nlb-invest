"""Verify dashboard calculations and failed-refresh preservation without network."""
import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from dashboard import data
from nlb_invest.models import FUNDS, NavPoint


class DashboardDataTests(unittest.TestCase):
    def test_refresh_matches_tracker_and_keeps_official_history(self):
        nav = [
            NavPoint(date(2026, 8, 18), 100, None, {}),
            NavPoint(date(2026, 8, 20), 110, None, {}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            with patch.object(data, "REPORT_PATH", report_path), patch.object(data, "extract_pdf_text", return_value="pdf"), patch.object(data, "parse_holdings", return_value=(date(2026, 7, 31), [])), patch.object(data, "NlbClient") as nlb, patch.object(data, "YahooClient"):
                nlb.return_value.get_nav_history.return_value = nav
                report = data.refresh_report(Path("report.pdf"), date(2026, 8, 19), {key: 1000 for key in FUNDS})
                self.assertAlmostEqual(report["investment_summary"]["estimated_current_value_eur"], 3300)
                self.assertAlmostEqual(report["investment_summary"]["estimated_gain_eur"], 300)
                self.assertEqual(report["funds"][0]["nav_history"][0]["date"], "2026-08-18")
                self.assertEqual(data.read_json(report_path), report)
                requested_start = nlb.return_value.get_nav_history.call_args.args[1]
                self.assertLessEqual(requested_start, date(2026, 8, 12))

    def test_failed_refresh_leaves_previous_report_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            data.write_json(report_path, {"previous": True})
            with patch.object(data, "REPORT_PATH", report_path), patch.object(data, "extract_pdf_text", side_effect=RuntimeError("Invalid PDF")):
                with self.assertRaisesRegex(RuntimeError, "Invalid PDF"):
                    data.refresh_report(Path("bad.pdf"), date(2026, 8, 19), {})
            self.assertEqual(data.read_json(report_path), {"previous": True})




@unittest.skipUnless(importlib.util.find_spec("streamlit") and importlib.util.find_spec("plotly"), "Optional dashboard dependencies are not installed")
class DashboardInterfaceTests(unittest.TestCase):
    def test_inputs_require_refresh_and_failed_refresh_keeps_results(self):
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
            with patch.object(data, "ROOT", local), patch.object(data, "LOCAL", local), patch.object(data, "SETTINGS_PATH", local / "settings.json"), patch.object(data, "load_report", return_value=report), patch.object(data, "load_settings", return_value=settings), patch.object(data, "refresh_report") as refresh:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py"), default_timeout=30).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.metric[0].value, "EUR 3,000.00")
                self.assertEqual(len(app.get("plotly_chart")), 1)
                app.number_input[0].set_value(2000).run()
                refresh.assert_not_called()
                self.assertEqual(app.metric[0].value, "EUR 3,000.00")
                refresh.side_effect = RuntimeError("Connection unavailable")
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertIn("Connection unavailable", app.error[0].value)
                self.assertEqual(app.metric[0].value, "EUR 3,000.00")
                self.assertEqual(refresh.call_args.args[2]["tech"], 2000)
                refresh.side_effect = None
                refresh.return_value = report
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(app.success)
                self.assertEqual(data.read_json(local / "settings.json")["amounts"]["tech"], 2000)

if __name__ == "__main__":
    unittest.main()
