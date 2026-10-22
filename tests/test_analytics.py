import unittest
from datetime import date
from unittest.mock import Mock

from nlb_invest.analytics import (
    add_months, analyze_fund, calculate_contribution_plan,
    calculate_since_date_analysis, nav_metrics, return_between,
)
from nlb_invest.models import FUNDS, Holding, MarketPoint, NavPoint


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
        with self.assertRaisesRegex(ValueError, "EUR 40 to EUR 500"):
            calculate_contribution_plan(
                points, 10.0, 1000.0, date(2026, 1, 9), 20.0, date(2026, 1, 10)
            )
        plan = calculate_contribution_plan(
            points, 10.0, 0.0, date(2026, 1, 1), 500.0, date(2026, 1, 10)
        )
        self.assertEqual(plan["monthly_contribution_count"], 1)
        self.assertIsNone(plan["initial_nav_date_used"])
        self.assertEqual(
            plan["contributions"][0]["fifteen_year_holding_date"], "2041-01-10"
        )
        with self.assertRaisesRegex(ValueError, "EUR 40 to EUR 500"):
            calculate_contribution_plan(
                points, 10.0, 0.0, date(2026, 1, 1), 501.0, date(2026, 1, 10)
            )
        future = calculate_contribution_plan(
            points, 10.0, 0.0, date(2026, 1, 1), 100.0, date(2026, 2, 10)
        )
        self.assertEqual(future["total_contributed_eur"], 0)
        self.assertEqual(future["accumulated_units"], 0)
        self.assertIsNone(future["estimated_return_pct"])

    def test_contribution_plan_changes_monthly_amount_and_builds_value_history(self):
        points = [
            NavPoint(date(2026, 1, 10), 10.0, None, {}),
            NavPoint(date(2026, 2, 10), 20.0, None, {}),
            NavPoint(date(2026, 3, 10), 25.0, None, {}),
        ]
        plan = calculate_contribution_plan(
            points, 25.0, 1000.0, date(2026, 1, 10), 100.0,
            date(2026, 2, 10),
            [{"effective_date": date(2026, 3, 1), "amount_eur": 150.0}],
        )
        self.assertEqual(
            [item["amount_eur"] for item in plan["contributions"]],
            [1000.0, 100.0, 150.0],
        )
        self.assertEqual(plan["monthly_changes"][0]["effective_date"], "2026-03-01")
        self.assertEqual(plan["value_history"][-1]["contributed_eur"], 1250.0)
        self.assertAlmostEqual(plan["value_history"][-1]["value_eur"], 2775.0)

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

    def test_holding_attribution_uses_each_monthly_snapshot_for_its_period(self):
        july = Holding(1, "US0000000001", "July holding", "ZDA", 100.0)
        august = Holding(1, "US0000000002", "August holding", "ZDA", 100.0)
        nav = [
            NavPoint(date(2026, 7, 31), 100.0, None, {}),
            NavPoint(date(2026, 8, 31), 110.0, None, {}),
            NavPoint(date(2026, 9, 30), 132.0, None, {}),
        ]
        yahoo = Mock()
        yahoo.resolve_isin.side_effect = lambda holding: holding.isin

        def eur_returns(symbol, start, official_nav_date, end, *, live):
            period_return = 0.10 if start == date(2026, 7, 31) else 0.20
            return (
                period_return, period_return, 0.0, "EUR", start, end,
                "2026-09-30T16:00:00+00:00",
            )

        yahoo.eur_returns.side_effect = eur_returns
        result = analyze_fund(
            FUNDS["tech"], date(2026, 8, 31), [august], nav, yahoo,
            date(2026, 9, 30), 90.0, 1, None, False, date(2026, 7, 31),
            [(date(2026, 7, 31), [july]), (date(2026, 8, 31), [august])],
        )

        self.assertEqual(result["holdings_snapshot_count"], 2)
        self.assertEqual(
            [period["holdings_date"] for period in result["holding_periods"]],
            ["2026-07-31", "2026-08-31"],
        )
        self.assertAlmostEqual(result["holding_periods"][0]["estimated_equity_contribution_pct"], 10.0)
        self.assertAlmostEqual(result["holding_periods"][1]["estimated_equity_contribution_pct"], 20.0)
        self.assertAlmostEqual(result["aligned_contribution_pct"], 32.0)
        self.assertAlmostEqual(result["official_return_since_holdings_pct"], 32.0)
        self.assertAlmostEqual(result["tracking_gap_pct"], 0.0)
