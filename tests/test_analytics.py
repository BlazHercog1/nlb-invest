import unittest
from datetime import date

from nlb_invest.analytics import (
    add_months, calculate_contribution_plan, calculate_since_date_analysis, nav_metrics,
    return_between,
)
from nlb_invest.models import MarketPoint, NavPoint


class AnalyticsTests(unittest.TestCase):
    def test_month_end_schedule_keeps_original_day_when_possible(self):
        start = date(2026, 1, 31)
        self.assertEqual(add_months(start, 1), date(2026, 2, 28))
        self.assertEqual(add_months(start, 2), date(2026, 3, 31))

    def test_contribution_plan_buys_units_at_each_available_nav(self):
        points = [
            NavPoint(date(2026, 1, 10), 10.0, None, {}),
            NavPoint(date(2026, 2, 11), 20.0, None, {}),
            NavPoint(date(2026, 3, 10), 25.0, None, {}),
        ]
        plan = calculate_contribution_plan(
            points,
            estimated_live_nav=30.0,
            initial_amount_eur=1000.0,
            initial_date=date(2026, 1, 10),
            monthly_amount_eur=100.0,
            monthly_start_date=date(2026, 2, 10),
        )
        self.assertEqual(plan["contribution_count"], 3)
        self.assertEqual(plan["contributions"][1]["nav_date_used"], "2026-02-11")
        self.assertAlmostEqual(plan["total_contributed_eur"], 1200.0)
        self.assertAlmostEqual(plan["accumulated_units"], 109.0)
        self.assertAlmostEqual(plan["estimated_current_value_eur"], 3270.0)
        self.assertAlmostEqual(plan["estimated_gain_eur"], 2070.0)

    def test_contribution_plan_validates_nlb_amount_limits(self):
        points = [NavPoint(date(2026, 1, 10), 10.0, None, {})]
        with self.assertRaisesRegex(ValueError, "at least EUR 1,000"):
            calculate_contribution_plan(points, 10.0, 999.0, date(2026, 1, 10))
        with self.assertRaisesRegex(ValueError, "EUR 40 to EUR 400"):
            calculate_contribution_plan(
                points, 10.0, 1000.0, date(2026, 1, 9), 20.0, date(2026, 1, 10)
            )

    def test_calculate_since_date_analysis(self):
        points = [
            NavPoint(date(2026, 8, 18), 9.5, 95.0, {}),
            NavPoint(date(2026, 8, 19), 10.0, 100.0, {}),
            NavPoint(date(2026, 8, 25), 10.2, 102.0, {}),
        ]
        analysis = calculate_since_date_analysis(
            points, date(2026, 8, 19), estimated_live_nav=10.3, invested_eur=1000
        )
        self.assertEqual(analysis["nav_date_used"], "2026-08-19")
        self.assertAlmostEqual(analysis["official_return_pct"], 2.0)
        self.assertAlmostEqual(analysis["estimated_live_return_pct"], 3.0)
        self.assertAlmostEqual(analysis["estimated_gain_per_1000_eur"], 30.0)
        self.assertAlmostEqual(analysis["estimated_current_value_for_investment_eur"], 1030.0)
        self.assertAlmostEqual(analysis["estimated_gain_for_investment_eur"], 30.0)

    def test_nav_metrics(self):
        points = [
            NavPoint(date(2026, 7, 31), 10.0, 100.0, {}),
            NavPoint(date(2026, 8, 1), 11.0, 110.0, {}),
            NavPoint(date(2026, 8, 2), 9.9, 99.0, {}),
        ]
        metrics = nav_metrics(points, date(2026, 8, 2), date(2026, 7, 31))
        self.assertAlmostEqual(metrics["since_holdings"], -0.01)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.10)

    def test_return_between_ignores_lead_in_and_future_points(self):
        points = [
            MarketPoint(date(2026, 7, 30), 90),
            MarketPoint(date(2026, 7, 31), 100),
            MarketPoint(date(2026, 8, 25), 105),
            MarketPoint(date(2026, 8, 27), 110),
        ]
        result, first_day, last_day = return_between(
            points, date(2026, 7, 31), date(2026, 8, 25)
        )
        self.assertAlmostEqual(result, 0.05)
        self.assertEqual(first_day, date(2026, 7, 31))
        self.assertEqual(last_day, date(2026, 8, 25))
