import unittest
from datetime import date

from nlb_invest.analytics import calculate_since_date_analysis, nav_metrics, return_between
from nlb_invest.models import MarketPoint, NavPoint


class AnalyticsTests(unittest.TestCase):
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
