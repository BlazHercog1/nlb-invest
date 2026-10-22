import unittest
import json
import tempfile
from datetime import date
from pathlib import Path

from nlb_invest.cli import load_investment_plans, parse_args


class ArgumentParserTests(unittest.TestCase):
    def test_accepts_developed_markets_investment(self):
        args = parse_args(["--fund", "developed", "--invested-developed", "1000"])
        self.assertEqual(args.funds, ["developed"])
        self.assertEqual(args.invested_developed, 1000.0)

    def test_accepts_multiple_monthly_reports(self):
        args = parse_args([
            "--pdf", "july.pdf", "--pdf", "august.pdf",
        ])

        self.assertEqual(args.pdf, [Path("july.pdf"), Path("august.pdf")])

    def test_loads_dashboard_monthly_contribution_settings(self):
        settings = {
            "plans": {
                "tech": {
                    "initial_amount_eur": 1050,
                    "initial_date": "2026-08-19",
                    "monthly_amount_eur": 50,
                    "monthly_start_date": "2026-09-15",
                    "monthly_changes": [{
                        "effective_date": "2027-01-15", "amount_eur": 75,
                    }],
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            plans = load_investment_plans(path)

        self.assertEqual(plans["tech"]["initial_date"], date(2026, 8, 19))
        self.assertEqual(plans["tech"]["monthly_start_date"], date(2026, 9, 15))
        self.assertEqual(plans["tech"]["monthly_amount_eur"], 50.0)
        self.assertEqual(
            plans["tech"]["monthly_changes"][0]["effective_date"],
            date(2027, 1, 15),
        )
