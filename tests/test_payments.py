"""Exercise cash-flow reconciliation, payment persistence and dashboard actions."""
import importlib.util
import json
import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from dashboard import data
from nlb_invest.analytics import calculate_contribution_plan
from nlb_invest.cli import load_investment_plans, main as cli_main
from nlb_invest.models import NavPoint
from nlb_invest.payments import monthly_story, normalize_payments, story_months


NAV = [
    NavPoint(date(2026, 1, 30), 10, None, {}),
    NavPoint(date(2026, 2, 2), 10, None, {}),
    NavPoint(date(2026, 2, 10), 10, None, {}),
    NavPoint(date(2026, 2, 27), 11, None, {}),
    NavPoint(date(2026, 3, 2), 11, None, {}),
    NavPoint(date(2026, 3, 31), 12, None, {}),
]
CONFIG = {
    "initial_amount_eur": 1000, "initial_date": "2026-01-30",
    "monthly_amount_eur": 100, "monthly_start_date": "2026-02-01",
    "monthly_changes": [],
}


def payment(identifier, **fields):
    kind, scheduled = identifier.split(":", 1)
    return {"id": identifier, "type": kind, "scheduled_date": scheduled, **fields}


def calculate(records=None, **overrides):
    values = dict(
        points=NAV, estimated_live_nav=13, initial_amount_eur=1000,
        initial_date=date(2026, 1, 30), monthly_amount_eur=100,
        monthly_start_date=date(2026, 2, 1), payment_records=records,
        as_of=date(2026, 3, 31),
    )
    values.update(overrides)
    return calculate_contribution_plan(**values)


def fund(plan, key="tech", latest="2026-03-31"):
    return {"key": key, "metrics": {"latest_date": latest}, "investment_plan": plan}


class PaymentCalculationTests(unittest.TestCase):
    def test_record_replaces_estimate_and_skipped_payment_buys_no_units(self):
        records = [
            payment("monthly:2026-02-01", status="confirmed", amount_eur=100,
                    actual_date="2026-02-02", fee_eur=2),
            payment("monthly:2026-03-01", status="skipped"),
            payment("extra:one", scheduled_date="2026-02-10", amount_eur=50,
                    status="confirmed", fee_eur=1, units=4.9),
        ]
        result = calculate(records)
        self.assertEqual(result["contribution_count"], 3)
        self.assertEqual(result["confirmed_payment_count"], 2)
        self.assertEqual(result["estimated_payment_count"], 1)
        self.assertEqual(result["skipped_payment_count"], 1)
        self.assertEqual(result["total_contributed_eur"], 1150)
        self.assertEqual(result["total_fees_eur"], 3)
        self.assertAlmostEqual(result["accumulated_units"], 114.7)
        self.assertAlmostEqual(result["official_value_eur"], 1376.4)
        self.assertAlmostEqual(sum(p["official_gain_eur"] for p in result["contributions"]), 226.4)
        self.assertAlmostEqual(sum(p["estimated_current_value_eur"] for p in result["contributions"]),
                               result["estimated_current_value_eur"])
        confirmed = next(p for p in result["contributions"] if p["type"] == "monthly")
        self.assertEqual(confirmed["units_source"], "estimated")
        self.assertAlmostEqual(confirmed["units"], 9.8)
        extra = next(p for p in result["contributions"] if p["type"] == "extra")
        self.assertEqual(extra["units_source"], "entered")
        self.assertAlmostEqual(extra["official_gain_eur"], 8.8)

    def test_delayed_purchase_is_pending_until_a_nav_exists(self):
        result = calculate([payment(
            "monthly:2026-03-01", status="confirmed", actual_date="2026-04-02",
        )])
        self.assertEqual(result["total_contributed_eur"], 1100)
        pending = next(p for p in result["payments"] if p["scheduled_date"] == "2026-03-01")
        self.assertEqual(pending["settlement"], "pending")
        self.assertIsNone(pending["official_value_eur"])
        self.assertEqual(result["pending_payment_count"], 1)

    def test_recorded_purchase_survives_removing_the_schedule(self):
        result = calculate([payment(
            "monthly:2026-02-01", status="confirmed", amount_eur=75, units=7.4,
        )], monthly_amount_eur=0)
        self.assertEqual(result["total_contributed_eur"], 1075)
        self.assertAlmostEqual(result["accumulated_units"], 107.4)

    def test_extra_only_plan_and_same_day_extra_are_supported(self):
        record = payment("extra:one", scheduled_date="2026-02-02", status="confirmed", amount_eur=60)
        only = calculate([record], initial_amount_eur=0, monthly_amount_eur=0)
        self.assertEqual(only["total_contributed_eur"], 60)
        self.assertEqual(only["contribution_count"], 1)
        self.assertEqual(calculate([record])["contribution_count"], 4)

    def test_historical_valuation_never_uses_later_nav_values(self):
        result = calculate(as_of=date(2026, 2, 28))
        self.assertEqual(result["total_contributed_eur"], 1100)
        self.assertAlmostEqual(result["official_value_eur"], 1210)
        self.assertEqual(result["value_history"][-1]["date"], "2026-02-27")

    def test_invalid_records_are_rejected(self):
        record = payment("monthly:2026-02-01", status="confirmed", amount_eur=100)
        for changes in ({"amount_eur": float("nan")}, {"fee_eur": 100},
                        {"units": float("inf")}, {"units": 0}, {"status": "paid"},
                        {"id": "monthly:2026-02-02"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                calculate([{**record, **changes}])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            normalize_payments([record, record])

    def test_monthly_story_separates_deposits_and_growth_and_includes_fees(self):
        result = calculate([
            payment("monthly:2026-02-01", status="confirmed", fee_eur=2),
            payment("monthly:2026-03-01", status="skipped"),
            payment("extra:one", scheduled_date="2026-02-10", status="confirmed",
                    amount_eur=50, fee_eur=1, units=4.9),
        ])
        february = monthly_story([fund(result)], "2026-02")
        self.assertEqual(february["opening_value_eur"], 1000)
        self.assertEqual(february["added_eur"], 150)
        self.assertAlmostEqual(february["closing_value_eur"], 1261.7)
        self.assertAlmostEqual(february["gain_eur"], 111.7)
        self.assertAlmostEqual(february["balance_change_eur"],
                               february["added_eur"] + february["gain_eur"])
        january = monthly_story([fund(result)], "2026-01")
        self.assertEqual(january["opening_value_eur"], 0)
        self.assertEqual(january["added_eur"], 1000)
        self.assertEqual(january["gain_eur"], 0)
        march = monthly_story([fund(result)], "2026-03")
        self.assertEqual(march["added_eur"], 0)

    def test_monthly_story_uses_a_common_published_date_across_funds(self):
        first = fund(calculate())
        second = fund(calculate(points=NAV[:-1]), "balanced", latest="2026-03-02")
        result = monthly_story([first, second], "2026-03")
        self.assertEqual(result["through"], "2026-03-02")
        self.assertEqual(result["added_eur"], 200)
        self.assertAlmostEqual(result["gain_eur"], 0)
        self.assertEqual(story_months([first, second]), ["2026-01", "2026-02", "2026-03"])


class TestDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 3, 31)


@contextmanager
def saved_dashboard():
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory)
        with (
            patch.object(data, "ROOT", local), patch.object(data, "LOCAL", local),
            patch.object(data, "SETTINGS_PATH", local / "settings.json"),
            patch.object(data, "REPORT_PATH", local / "report.json"),
            patch.object(data, "TEXT_REPORT_PATH", local / "latest_report.txt"),
            patch.object(data, "date", TestDate),
            patch.object(data, "extract_pdf_text", return_value="pdf"),
            patch.object(data, "parse_holdings", return_value=(date(2026, 1, 30), [])),
            patch.object(data, "NlbClient") as nlb, patch.object(data, "YahooClient"),
        ):
            nlb.return_value.get_nav_history.return_value = NAV
            # refresh_report normalizes JSON-style plan dates.
            report = data.refresh_report(Path("report.pdf"), date(2026, 1, 30),
                                         {}, True, {"tech": deepcopy(CONFIG)})
            settings = {"plans": {"tech": deepcopy(CONFIG)}, "pdfs": ["report.pdf"],
                        "return_since": "2026-01-30", "live": True}
            data.write_json(data.SETTINGS_PATH, settings)
            (local / "report.pdf").write_bytes(b"test")
            yield report, settings, local


class PaymentStorageTests(unittest.TestCase):
    def test_save_and_reset_update_reports_and_preserve_other_settings(self):
        with saved_dashboard() as (report, settings, local):
            original = deepcopy(report)
            updated = data.save_payment_change(report, settings, "tech", payment(
                "monthly:2026-02-01", status="skipped", amount_eur=100,
            ))
            self.assertEqual(report, original)
            tech = updated["funds"][0]
            self.assertEqual(tech["invested_eur"], 1100)
            self.assertEqual(tech["investment_plan"]["skipped_payment_count"], 1)
            stored = data.read_json(data.SETTINGS_PATH)
            self.assertEqual(stored["pdfs"], ["report.pdf"])
            loaded = load_investment_plans(data.SETTINGS_PATH)
            self.assertEqual(loaded["tech"]["payments"][0]["status"], "skipped")
            self.assertEqual(data.read_json(data.REPORT_PATH), updated)
            text = (local / "latest_report.txt").read_text(encoding="utf-8")
            self.assertIn("[skipped; skipped]", text)
            self.assertIn("Monthly story 2026-03", text)
            restored = data.save_payment_change(updated, stored, "tech",
                                                reset_id="monthly:2026-02-01")
            self.assertEqual(restored["funds"][0]["invested_eur"], 1200)
            self.assertEqual(restored["funds"][0]["investment_plan"]["payment_records"], [])

    def test_scheduled_cli_uses_the_same_recorded_payment_values(self):
        from nlb_invest import cli
        with saved_dashboard() as (report, settings, local):
            updated = data.save_payment_change(report, settings, "tech", payment(
                "monthly:2026-02-01", status="confirmed", amount_eur=80, fee_eur=2, units=7.8,
            ))
            output = StringIO()
            with (
                patch.object(cli, "extract_pdf_text", return_value="pdf"),
                patch.object(cli, "parse_holdings", return_value=(date(2026, 1, 30), [])),
                patch.object(cli, "NlbClient") as nlb, patch.object(cli, "YahooClient"),
                patch("sys.stdout", output),
            ):
                nlb.return_value.get_nav_history.return_value = NAV
                result = cli_main([
                    "--pdf", str(local / "report.pdf"), "--fund", "tech",
                    "--as-of", "2026-03-31", "--return-since", "2026-01-30",
                    "--investment-settings", str(data.SETTINGS_PATH),
                    "--latest-report", str(local / "cli.txt"), "--format", "json",
                ])
            self.assertEqual(result, 0)
            tracker = json.loads(output.getvalue())
            self.assertEqual(tracker["funds"][0]["nav_history"],
                             updated["funds"][0]["nav_history"])
            self.assertEqual(tracker["funds"][0]["investment_plan"],
                             updated["funds"][0]["investment_plan"])

    def test_removing_the_last_extra_only_payment_clears_its_balance(self):
        with saved_dashboard() as (report, settings, local):
            settings["plans"]["tech"].update(initial_amount_eur=0, monthly_amount_eur=0)
            updated = data.save_payment_change(report, settings, "tech", payment(
                "extra:only", scheduled_date="2026-02-10", status="confirmed", amount_eur=60,
            ))
            self.assertEqual(updated["funds"][0]["invested_eur"], 60)
            updated = data.save_payment_change(updated, data.read_json(data.SETTINGS_PATH),
                                               "tech", reset_id="extra:only")
            self.assertIsNone(updated["funds"][0]["invested_eur"])
            self.assertNotIn("investment_plan", updated["funds"][0])

    def test_invalid_edit_does_not_change_saved_files(self):
        with saved_dashboard() as (report, settings, local):
            paths = [data.SETTINGS_PATH, data.REPORT_PATH, data.TEXT_REPORT_PATH]
            before = [path.read_bytes() for path in paths]
            with self.assertRaises(ValueError):
                data.save_payment_change(report, settings, "tech", payment(
                    "monthly:2026-02-01", amount_eur=50, fee_eur=60,
                ))
            self.assertEqual([path.read_bytes() for path in paths], before)

    def test_failed_file_replace_rolls_back_settings_and_report(self):
        with saved_dashboard() as (report, settings, local):
            paths = [data.SETTINGS_PATH, data.REPORT_PATH, data.TEXT_REPORT_PATH]
            before = [path.read_bytes() for path in paths]
            original_replace = Path.replace

            def fail_report(path, target):
                if target == data.REPORT_PATH:
                    raise OSError("Disk unavailable")
                return original_replace(path, target)

            with patch.object(Path, "replace", fail_report), self.assertRaises(OSError):
                data.save_payment_change(report, settings, "tech", payment(
                    "monthly:2026-02-01", status="skipped", amount_eur=100,
                ))
            self.assertEqual([path.read_bytes() for path in paths], before)
            self.assertFalse(list(local.glob(".*.tmp")))


@unittest.skipUnless(importlib.util.find_spec("streamlit") and importlib.util.find_spec("plotly"),
                     "Dashboard dependencies are not installed")
class PaymentInterfaceTests(unittest.TestCase):
    def test_confirm_skip_restore_edit_and_extra_payment_persist_without_refresh(self):
        from streamlit.testing.v1 import AppTest
        app_path = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
        with saved_dashboard() as (report, settings, local), patch.object(
                data, "refresh_report", side_effect=AssertionError("Network refresh is unexpected")):
            app = AppTest.from_file(app_path, default_timeout=30).run()
            self.assertEqual(len(app.exception), 0)
            app.selectbox(key="payment_tech").select("monthly:2026-02-01").run()
            app.button(key="confirm_tech").click().run()
            self.assertEqual(len(app.exception), 0)
            plan = data.read_json(data.REPORT_PATH)["funds"][0]["investment_plan"]
            self.assertEqual(plan["confirmed_payment_count"], 1)
            self.assertEqual(plan["contributions"][1]["units_source"], "estimated")
            app.button(key="skip_tech").click().run()
            self.assertEqual(data.read_json(data.REPORT_PATH)["funds"][0]["invested_eur"], 1100)
            app.button(key="reset_tech").click().run()
            self.assertEqual(data.read_json(data.REPORT_PATH)["funds"][0]["invested_eur"], 1200)
            next(w for w in app.number_input if w.label == "Amount paid (EUR)").set_value(80)
            next(w for w in app.number_input if w.label == "Fee included in amount paid (EUR)").set_value(2)
            next(w for w in app.number_input if w.label == "Units received (optional)").set_value(7.8)
            next(w for w in app.selectbox if w.label == "Payment status").select("Confirmed")
            next(w for w in app.button if w.label == "Save payment").click().run()
            self.assertEqual(len(app.exception), 0)
            plan = data.read_json(data.REPORT_PATH)["funds"][0]["investment_plan"]
            self.assertEqual(plan["total_contributed_eur"], 1180)
            self.assertAlmostEqual(plan["contributions"][1]["units"], 7.8)
            app.date_input(key="extra_date_tech").set_value(date(2026, 2, 10))
            app.number_input(key="extra_amount_tech").set_value(60)
            next(w for w in app.button if w.label == "Add extra payment").click().run()
            self.assertEqual(len(app.exception), 0)
            saved = data.read_json(data.REPORT_PATH)
            self.assertEqual(saved["funds"][0]["invested_eur"], 1240)
            self.assertEqual(len(saved["funds"][0]["investment_plan"]["payment_records"]), 2)
            transaction_table = next(frame.value for frame in app.dataframe
                                     if "Current value (EUR)" in frame.value.columns)
            self.assertEqual(len(transaction_table), 4)
            self.assertIn("Return (%)", transaction_table.columns)
            app.selectbox(key="story_month").select("2026-02").run()
            self.assertEqual(next(m.value for m in app.metric if m.label == "Added this month"),
                             "EUR 140.00")
            reopened = AppTest.from_file(app_path, default_timeout=30).run()
            self.assertEqual(len(reopened.exception), 0)
            self.assertEqual(reopened.session_state["report"]["funds"][0]["invested_eur"], 1240)
