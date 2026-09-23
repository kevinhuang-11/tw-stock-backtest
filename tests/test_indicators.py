import unittest
from decimal import Decimal

from indicators import simple_moving_average


class TestSimpleMovingAverage(unittest.TestCase):
    def setUp(self):
        self.prices = [
            Decimal("10"),
            Decimal("20"),
            Decimal("30"),
            Decimal("40"),
            Decimal("50"),
        ]

    def test_three_day_average(self):
        result = simple_moving_average(self.prices, 3)

        self.assertEqual(
            result,
            [
                None,
                None,
                Decimal("20"),
                Decimal("30"),
                Decimal("40"),
            ],
        )

    def test_period_one(self):
        result = simple_moving_average(self.prices, 1)

        self.assertEqual(result, self.prices)

    def test_insufficient_data(self):
        result = simple_moving_average(self.prices[:2], 3)

        self.assertEqual(result, [None, None])

    def test_empty_prices(self):
        self.assertEqual(simple_moving_average([], 3), [])

    def test_invalid_period(self):
        for period in (0, -1, 2.5, True):
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    simple_moving_average(self.prices, period)

    def test_invalid_prices(self):
        for price in (
            None,
            10.0,
            Decimal("0"),
            Decimal("-1"),
            Decimal("NaN"),
            Decimal("Infinity"),
        ):
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    simple_moving_average([price], 1)

    def test_future_price_does_not_change_past_average(self):
        original = simple_moving_average(self.prices, 3)

        changed_prices = self.prices.copy()
        changed_prices[-1] = Decimal("5000")

        changed = simple_moving_average(changed_prices, 3)

        # 只修改最後一天，不應影響前面的均線。
        self.assertEqual(original[:-1], changed[:-1])
        self.assertNotEqual(original[-1], changed[-1])


if __name__ == "__main__":
    unittest.main()