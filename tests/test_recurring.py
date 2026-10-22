"""Regression checks for recurring payments and stale dashboard snapshots."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from dashboard import data
from nlb_invest.analytics import calculate_contribution_plan
from nlb_invest.models import NavPoint
from nlb_invest.payments import monthly_schedule
from tests.test_payments import saved_dashboard


OCTOBER_NAV = [
    NavPoint(date(2026, 8, 19), 10, None, {}),
    NavPoint(date(2026, 9, 15), 11, None, {}),
    NavPoint(date(2026, 10, 7), 12, None, {}),
    NavPoint(date(2026, 10, 15), 13, None, {}),
    NavPoint(date(2026, 10, 20), 14, None, {}),
]
PLAN = {
    "initial_amount_eur": 1050, "initial_date": "2026-08-19",
    "monthly_amount_eur": 50, "monthly_start_date": "2026-09-15",
}


def october_plan(points=OCTOBER_NAV, through=date(2026, 10, 22), records=None):
    return calculate_contribution_plan(
        points, points[-1].nav, 1050, date(2026, 8, 19),
        50, date(2026, 9, 15), payment_records=records, as_of=through,
    )


def snapshot(plan, as_of="2026-10-09"):
    return {"as_of": as_of, "funds": [{"key": "tech", "investment_plan": plan}]}


class RecurringScheduleTests(TestCase):
    def test_fifteenth_repeats_in_october_without_including_november(self):
        plan = october_plan()
        monthly = [p for p in plan["contributions"] if p["type"] == "monthly"]
        self.assertEqual([p["scheduled_date"] for p in monthly],
                         ["2026-09-15", "2026-10-15"])
        self.assertEqual([p["amount_eur"] for p in monthly], [50, 50])
        self.assertEqual(plan["total_contributed_eur"], 1150)
        self.assertEqual(plan["contribution_count"], 3)
        before = october_plan(through=date(2026, 10, 14))
        self.assertEqual(before["monthly_contribution_count"], 1)
        on_day = october_plan(through=date(2026, 10, 15))
        self.assertEqual(on_day["monthly_contribution_count"], 2)

    def test_overview_distinguishes_first_latest_and_next_payment(self):
        schedule = monthly_schedule(PLAN, date(2026, 10, 22))
        self.assertEqual(schedule["first_date"], "2026-09-15")
        self.assertEqual(schedule["last_due_date"], "2026-10-15")
        self.assertEqual(schedule["next_date"], "2026-11-15")
        self.assertEqual(schedule["next_amount_eur"], 50)
        future = monthly_schedule(PLAN, date(2026, 9, 1))
        self.assertIsNone(future["last_due_date"])
        self.assertEqual(future["next_date"], "2026-09-15")

    def test_short_month_keeps_the_original_calendar_day_and_amount_changes(self):
        plan = {**PLAN, "monthly_start_date": "2026-01-31",
                "monthly_changes": [{"effective_date": "2026-03-01", "amount_eur": 75}]}
        schedule = monthly_schedule(plan, date(2026, 3, 30))
        self.assertEqual(schedule["last_due_date"], "2026-02-28")
        self.assertEqual(schedule["next_date"], "2026-03-31")
        self.assertEqual(schedule["last_due_amount_eur"], 50)
        self.assertEqual(schedule["next_amount_eur"], 75)

    def test_old_snapshot_catches_up_after_payment_day(self):
        old = snapshot(october_plan(OCTOBER_NAV[:3], date(2026, 10, 9)))
        settings = {"plans": {"tech": PLAN}}
        self.assertFalse(data.recurring_refresh_needed(old, settings, date(2026, 10, 14)))
        self.assertTrue(data.recurring_refresh_needed(old, settings, date(2026, 10, 15)))
        self.assertTrue(data.recurring_refresh_needed(old, settings, date(2026, 10, 22)))

    def test_skipped_and_delayed_purchases_do_not_trigger_a_price_fetch(self):
        old = snapshot(october_plan(OCTOBER_NAV[:3], date(2026, 10, 9)))
        for override in ({"status": "skipped"}, {"status": "confirmed", "actual_date": "2026-11-01"}):
            settings = {"plans": {"tech": {**PLAN, "payments": [{
                "id": "monthly:2026-10-15", "scheduled_date": "2026-10-15",
                "type": "monthly", "amount_eur": 50, **override,
            }]}}}
            self.assertFalse(data.recurring_refresh_needed(old, settings, date(2026, 10, 22)))

    def test_pending_purchase_retries_the_next_day(self):
        old = snapshot(october_plan(OCTOBER_NAV[:3], date(2026, 10, 15)), "2026-10-15")
        settings = {"plans": {"tech": PLAN}}
        self.assertFalse(data.recurring_refresh_needed(old, settings, date(2026, 10, 15)))
        self.assertTrue(data.recurring_refresh_needed(old, settings, date(2026, 10, 16)))

    def test_offline_catchup_exposes_due_payment_without_reusing_old_price(self):
        with saved_dashboard() as (report, settings, local):
            # Keep only prices through February; the March payment is now due.
            old = deepcopy(report)
            old["as_of"] = "2026-02-10"
            old["funds"][0]["nav_history"] = old["funds"][0]["nav_history"][:3]
            updated = data.show_due_payments(old, settings, date(2026, 3, 31))
            plan = updated["funds"][0]["investment_plan"]
            march = next(p for p in plan["payments"] if p["scheduled_date"] == "2026-03-01")
            self.assertEqual(march["settlement"], "pending")
            self.assertIsNone(march["units"])
            self.assertIsNone(march["official_value_eur"])
            self.assertEqual(plan["total_contributed_eur"], 1100)
            self.assertEqual(old["funds"][0]["invested_eur"], 1200)

    def test_newer_saved_snapshot_replaces_open_session_snapshot(self):
        current = {"generated_at": "2026-10-09T10:00:00+02:00"}
        saved = {"generated_at": "2026-10-22T10:00:00+02:00"}
        self.assertIs(data.prefer_newer_report(current, saved), saved)
        self.assertIs(data.prefer_newer_report(saved, current), saved)


class FebruaryDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 2, 10)


def make_old_report(settings):
    with patch.object(data, "date", FebruaryDate):
        return data.refresh_report(
            Path("report.pdf"), date(2026, 1, 30), {}, True, settings["plans"],
        )


class RecurringDashboardTests(TestCase):
    def test_dashboard_catches_up_on_open_and_defaults_to_latest_payment(self):
        from streamlit.testing.v1 import AppTest
        app_path = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
        with saved_dashboard() as (report, settings, local):
            make_old_report(settings)
            with patch.object(data, "refresh_recurring_report", wraps=data.refresh_recurring_report) as refresh:
                app = AppTest.from_file(app_path, default_timeout=30).run()
                self.assertEqual(len(app.exception), 0)
                refresh.assert_called_once()
                current = app.session_state["report"]["funds"][0]
                self.assertEqual(current["invested_eur"], 1200)
                self.assertEqual(current["investment_plan"]["monthly_contribution_count"], 2)
                self.assertEqual(app.selectbox(key="payment_tech").value, "monthly:2026-03-01")
                self.assertTrue(any("Latest monthly payment included: 2026-03-01" in c.value
                                    for c in app.caption))
                app.run()
                refresh.assert_called_once()

    def test_failed_catchup_keeps_due_payment_visible_and_avoids_repeated_requests(self):
        from streamlit.testing.v1 import AppTest
        app_path = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
        with saved_dashboard() as (report, settings, local):
            make_old_report(settings)
            before = data.REPORT_PATH.read_bytes()
            with patch.object(data, "refresh_recurring_report",
                              side_effect=RuntimeError("Offline")) as refresh:
                app = AppTest.from_file(app_path, default_timeout=30).run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("pending NAV" in w.value for w in app.warning))
                rows = next(frame.value for frame in app.dataframe if "Status" in frame.value.columns)
                self.assertTrue(any("pending NAV" in status for status in rows["Status"]))
                self.assertEqual(data.REPORT_PATH.read_bytes(), before)
                app.run()
                refresh.assert_called_once()

    def test_open_dashboard_picks_up_a_scheduled_snapshot(self):
        from streamlit.testing.v1 import AppTest
        app_path = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
        with saved_dashboard() as (report, settings, local):
            app = AppTest.from_file(app_path, default_timeout=30).run()
            updated = deepcopy(report)
            timestamp = datetime.fromisoformat(report["generated_at"]) + timedelta(minutes=1)
            updated["generated_at"] = timestamp.isoformat()
            data.write_json(data.REPORT_PATH, updated)
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["report"]["generated_at"], updated["generated_at"])
