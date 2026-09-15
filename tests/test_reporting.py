import json
import unittest
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from nlb_invest.cli import main


class ReportPrivacyTests(unittest.TestCase):
    def test_json_report_exposes_only_pdf_filename(self):
        output = StringIO()
        pdf_path = Path("private") / "source_documents" / "monthly_report.pdf"
        with (
            patch("nlb_invest.cli.Path.exists", return_value=True),
            patch("nlb_invest.cli.Path.write_text") as write_text,
            patch("nlb_invest.cli.extract_pdf_text", return_value="PDF text"),
            patch(
                "nlb_invest.cli.parse_holdings",
                return_value=(date(2026, 7, 31), []),
            ),
            patch("nlb_invest.cli.NlbClient"),
            patch("nlb_invest.cli.YahooClient"),
            patch("nlb_invest.cli.analyze_fund", return_value={"invested_eur": None}),
            patch("nlb_invest.cli.render_text", return_value="Dashboard\n"),
            patch("sys.stdout", output),
        ):
            result = main(["--pdf", str(pdf_path), "--fund", "tech", "--format", "json"])

        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["source_pdf"], "monthly_report.pdf")
        self.assertNotIn("source_documents", output.getvalue())
        self.assertNotIn("private", output.getvalue())
        write_text.assert_called_once_with("Dashboard\n", encoding="utf-8")
