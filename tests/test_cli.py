import unittest

from nlb_invest.cli import parse_args


class ArgumentParserTests(unittest.TestCase):
    def test_accepts_developed_markets_investment(self):
        args = parse_args(["--fund", "developed", "--invested-developed", "1000"])
        self.assertEqual(args.funds, ["developed"])
        self.assertEqual(args.invested_developed, 1000.0)
