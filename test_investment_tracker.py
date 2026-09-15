import json
import unittest
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from investment_tracker import (
    FUNDS,
    MarketPoint,
    NavPoint,
    calculate_since_date_analysis,
    convert_to_eur,
    extract_pdf_text,
    main,
    nav_metrics,
    parse_args,
    parse_holdings,
    return_between,
)


class PdfParserTests(unittest.TestCase):
    @patch("pypdf.PdfReader")
    def test_extracts_pdf_in_layout_mode_without_external_program(self, reader_class):
        first_page = MagicMock()
        first_page.extract_text.return_value = "First–page"
        second_page = MagicMock()
        second_page.extract_text.return_value = None
        reader_class.return_value.pages = [first_page, second_page]

        text = extract_pdf_text(Path("report.pdf"))

        reader_class.assert_called_once_with(Path("report.pdf"))
        first_page.extract_text.assert_called_once_with(extraction_mode="layout")
        second_page.extract_text.assert_called_once_with(extraction_mode="layout")
        self.assertEqual(text, "First-page\n\f\n")

    def test_parses_wrapped_percentage_and_stops_at_restarted_table(self):
        text = """
NLB Skladi - Visoka tehnologija delniški
Naložbe podsklada v delnice in enote delniških ciljnih skladov na dan 31.07.2026
1    KR7000660001   SK HYNIX INC                              JUŽNA KOREJA       6,22%
2    US67066G1040   NVIDIA CORP                               ZDA                0,1
6%
1    XS0000000001   SOME BOND                                 ZDA                2,00%
"""
        report_date, holdings = parse_holdings(text, FUNDS["tech"])
        self.assertEqual(report_date, date(2026, 7, 31))
        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[1].issuer, "NVIDIA CORP")
        self.assertAlmostEqual(holdings[1].weight_pct, 0.16)

    def test_parses_developed_markets_fund(self):
        text = """
NLB Skladi - Svetovni razviti trgi delniški
Naložbe podsklada v delnice in enote delniških ciljnih skladov na dan 31.07.2026
1    US0378331005   APPLE INC                                  ZDA                5,25%
"""
        report_date, holdings = parse_holdings(text, FUNDS["developed"])
        self.assertEqual(report_date, date(2026, 7, 31))
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].issuer, "APPLE INC")
        self.assertAlmostEqual(holdings[0].weight_pct, 5.25)


class ArgumentParserTests(unittest.TestCase):
    def test_accepts_developed_markets_investment(self):
        args = parse_args(["--fund", "developed", "--invested-developed", "1000"])
        self.assertEqual(args.funds, ["developed"])
        self.assertEqual(args.invested_developed, 1000.0)


class ReportPrivacyTests(unittest.TestCase):
    def test_json_report_exposes_only_pdf_filename(self):
        output = StringIO()
        pdf_path = Path("private") / "source_documents" / "monthly_report.pdf"
        with (
            patch("investment_tracker.Path.exists", return_value=True),
            patch("investment_tracker.Path.write_text") as write_text,
            patch("investment_tracker.extract_pdf_text", return_value="PDF text"),
            patch(
                "investment_tracker.parse_holdings",
                return_value=(date(2026, 7, 31), []),
            ),
            patch("investment_tracker.NlbClient"),
            patch("investment_tracker.YahooClient"),
            patch("investment_tracker.analyze_fund", return_value={"invested_eur": None}),
            patch("investment_tracker.render_text", return_value="Dashboard\n"),
            patch("sys.stdout", output),
        ):
            result = main(["--pdf", str(pdf_path), "--fund", "tech", "--format", "json"])

        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["source_pdf"], "monthly_report.pdf")
        self.assertNotIn("source_documents", output.getvalue())
        self.assertNotIn("private", output.getvalue())
        write_text.assert_called_once_with("Dashboard\n", encoding="utf-8")


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

    def test_currency_conversion_uses_previous_available_fx_day(self):
        prices = [MarketPoint(date(2026, 8, 1), 100), MarketPoint(date(2026, 8, 3), 110)]
        fx = [MarketPoint(date(2026, 7, 31), 0.9), MarketPoint(date(2026, 8, 2), 0.8)]
        converted = convert_to_eur(prices, fx)
        self.assertEqual([point.adjusted_close for point in converted], [90, 88])

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


if __name__ == "__main__":
    unittest.main()
