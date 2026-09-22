import unittest
from decimal import Decimal

from stock_data import parse_twse_date, parse_price, parse_volume


class TestParseTwseDate(unittest.TestCase):
    def test_normal_date(self):
        result = parse_twse_date("114/01/02")
        self.assertEqual(result, "2025-01-02")

    def test_leap_day(self):
        result = parse_twse_date("113/02/29")
        self.assertEqual(result, "2024-02-29")

    def test_invalid_date(self):
        with self.assertRaises(ValueError):
            parse_twse_date("114/02/29")


class TestParsePrice(unittest.TestCase):
    def test_price_with_comma(self):
        self.assertEqual(
            parse_price("1,070.00"),
            Decimal("1070.00"),
        )

    def test_missing_price(self):
        self.assertIsNone(parse_price("--"))

    def test_invalid_prices(self):
        for value in ("abc", "NaN", "Infinity", "-10.00", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_price(value)


class TestParseVolume(unittest.TestCase):
    def test_volume_with_comma(self):
        self.assertEqual(parse_volume("45,045,125"), 45045125)

    def test_zero_volume(self):
        self.assertEqual(parse_volume("0"), 0)

    def test_invalid_volumes(self):
        for value in ("-1", "1.5", "--", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_volume(value)




if __name__ == "__main__":
    unittest.main()