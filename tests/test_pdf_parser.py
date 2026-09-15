import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from nlb_invest.models import FUNDS
from nlb_invest.pdf_parser import extract_pdf_text, parse_holdings


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
