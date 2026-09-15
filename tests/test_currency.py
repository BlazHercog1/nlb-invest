import unittest
from datetime import date

from nlb_invest.currency import convert_to_eur
from nlb_invest.models import MarketPoint


class CurrencyTests(unittest.TestCase):
    def test_currency_conversion_uses_previous_available_fx_day(self):
        prices = [MarketPoint(date(2026, 8, 1), 100), MarketPoint(date(2026, 8, 3), 110)]
        fx = [MarketPoint(date(2026, 7, 31), 0.9), MarketPoint(date(2026, 8, 2), 0.8)]
        converted = convert_to_eur(prices, fx)
        self.assertEqual([point.adjusted_close for point in converted], [90, 88])
