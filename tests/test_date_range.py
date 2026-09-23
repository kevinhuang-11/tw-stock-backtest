import unittest
from datetime import date

from tw_stock_backtest.date_range import (
    parse_date_range,
    month_starts,
    filter_records,
)


class TestDateRange(unittest.TestCase):
    def test_parse_dates(self):
        start, end = parse_date_range("2026-08-15", "2026-09-22")

        self.assertEqual(start, date(2026, 8, 15))
        self.assertEqual(end, date(2026, 9, 22))

    def test_invalid_date(self):
        with self.assertRaises(ValueError):
            parse_date_range("2026-02-30", "2026-03-01")

    def test_start_after_end(self):
        with self.assertRaises(ValueError):
            parse_date_range("2026-09-22", "2026-09-01")

    def test_same_month(self):
        result = list(
            month_starts(date(2026, 9, 2), date(2026, 9, 22))
        )

        self.assertEqual(result, [date(2026, 9, 1)])

    def test_cross_year(self):
        result = list(
            month_starts(date(2025, 12, 15), date(2026, 2, 10))
        )

        self.assertEqual(
            result,
            [
                date(2025, 12, 1),
                date(2026, 1, 1),
                date(2026, 2, 1),
            ],
        )

    def test_filter_includes_start_and_end(self):
        records = [
            {"date": "2026-08-14"},
            {"date": "2026-08-15"},
            {"date": "2026-09-22"},
            {"date": "2026-09-23"},
        ]

        result = filter_records(
            records,
            date(2026, 8, 15),
            date(2026, 9, 22),
        )

        self.assertEqual(
            result,
            [
                {"date": "2026-08-15"},
                {"date": "2026-09-22"},
            ],
        )

    def test_single_day(self):
        records = [
            {"date": "2026-09-01"},
            {"date": "2026-09-02"},
        ]

        result = filter_records(
            records,
            date(2026, 9, 2),
            date(2026, 9, 2),
        )

        self.assertEqual(result, [{"date": "2026-09-02"}])

    def test_no_matching_records(self):
        result = filter_records(
            [{"date": "2026-08-31"}],
            date(2026, 9, 1),
            date(2026, 9, 2),
        )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()