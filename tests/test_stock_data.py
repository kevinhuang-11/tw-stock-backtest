import unittest
from decimal import Decimal

from tw_stock_backtest.data.stock_data import parse_twse_date, parse_price, parse_volume
from tw_stock_backtest.data.stock_data import normalize_row
from tw_stock_backtest.data.stock_data import parse_nonnegative_integer



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

class TestNormalizeRow(unittest.TestCase):
    def setUp(self):
        self.fields = [
            "日期",
            "成交股數",
            "成交金額",
            "開盤價",
            "最高價",
            "最低價",
            "收盤價",
            "成交筆數",
        ]

        self.row = [
            "114/01/02",
            "45,045,125",
            "47,883,206,644",
            "1,070.00",
            "1,075.00",
            "1,055.00",
            "1,065.00",
            "74,997",
        ]

    def test_normal_row(self):
        result = normalize_row("2330", self.fields, self.row)

        expected = {
            "stock_id": "2330",
            "date": "2025-01-02",
            "open": Decimal("1070.00"),
            "high": Decimal("1075.00"),
            "low": Decimal("1055.00"),
            "close": Decimal("1065.00"),
            "volume": 45045125,
            "turnover": 47883206644,
            "trade_count": 74997,
        }

        self.assertEqual(result, expected)

    def test_reordered_fields(self):
        normal = normalize_row("2330", self.fields, self.row)

        reordered = normalize_row(
            "2330",
            self.fields[::-1],
            self.row[::-1],
        )

        self.assertEqual(reordered, normal)

    def test_mismatched_length(self):
        with self.assertRaises(ValueError):
            normalize_row("2330", self.fields, self.row[:-1])

    def test_missing_field(self):
        with self.assertRaises(ValueError):
            normalize_row("2330", self.fields[:-1], self.row[:-1])

    def test_invalid_price_range(self):
        index = self.fields.index("收盤價")
        self.row[index] = "1,100.00"

        with self.assertRaises(ValueError):
            normalize_row("2330", self.fields, self.row)

    def test_missing_prices(self):
        for field in ("開盤價", "最高價", "最低價", "收盤價"):
            self.row[self.fields.index(field)] = "--"

        for field in ("成交股數", "成交金額", "成交筆數"):
            self.row[self.fields.index(field)] = "0"

        result = normalize_row("2330", self.fields, self.row)

        for key in ("open", "high", "low", "close"):
            self.assertIsNone(result[key])

        self.assertEqual(result["volume"], 0)
        self.assertEqual(result["turnover"], 0)
        self.assertEqual(result["trade_count"], 0)

    def test_invalid_turnover_and_trade_count(self):
        for field in ("成交金額", "成交筆數"):
            for value in ("-1", "1.5", "--", ""):
                with self.subTest(field=field, value=value):
                    row = self.row.copy()
                    row[self.fields.index(field)] = value

                    with self.assertRaises(ValueError):
                        normalize_row("2330", self.fields, row)


if __name__ == "__main__":
    unittest.main()