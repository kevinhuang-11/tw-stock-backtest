import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.analysis.screening_history import (
    build_screening_history,
)


class TestScreeningHistory(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "short_window": 2,
            "long_window": 3,
            "volume_window": 2,
            "min_volume_ratio": Decimal("1"),
            "momentum_window": 2,
            "top_n": 1,
        }

    def make_records(self, stock_id, prices):
        start = date(2026, 1, 1)

        return [
            {
                "stock_id": stock_id,
                "date": (
                    start + timedelta(days=index)
                ).isoformat(),
                "close": Decimal(str(price)),
                "volume": 100 * (2 ** index),
            }
            for index, price in enumerate(prices)
        ]

    def build(self, records, start="2026-01-03", end="2026-01-04"):
        return build_screening_history(
            records,
            start,
            end,
            screening_settings=self.settings,
        )

    def test_daily_ranking(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12, 13]),
            "BBB": self.make_records("BBB", [10, 12, 14, 16]),
        }

        history = self.build(records)

        self.assertEqual(
            [day["date"] for day in history],
            ["2026-01-03", "2026-01-04"],
        )

        for day in history:
            self.assertEqual(day["evaluated_count"], 2)
            self.assertEqual(day["matched_count"], 2)
            self.assertEqual(len(day["candidates"]), 1)
            self.assertEqual(
                day["candidates"][0]["stock_id"],
                "BBB",
            )

    def test_tie_is_sorted_by_stock_id(self):
        records = {
            "BBB": self.make_records("BBB", [10, 11, 12]),
            "AAA": self.make_records("AAA", [10, 11, 12]),
        }

        history = self.build(records, end="2026-01-03")

        self.assertEqual(
            history[0]["candidates"][0]["stock_id"],
            "AAA",
        )

    def test_uses_history_before_start(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12]),
        }

        history = self.build(records, end="2026-01-03")

        # 分析期間只有一天，但之前兩天可作為暖機資料。
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["evaluated_count"], 1)
        self.assertEqual(
            history[0]["candidates"][0]["short_ma"],
            Decimal("11.5"),
        )

    def test_missing_date_is_reported(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12, 13]),
            "BBB": self.make_records("BBB", [10, 12, 14]),
        }

        history = self.build(records)
        last_day = history[-1]

        self.assertEqual(last_day["evaluated_count"], 1)
        self.assertIn("BBB", last_day["errors"])

    def test_future_data_does_not_change_past(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12, 13]),
        }

        original = self.build(records)

        records["AAA"].append(
            {
                "stock_id": "AAA",
                "date": "2026-01-05",
                "close": Decimal("9999"),
                "volume": 999999,
            }
        )

        self.assertEqual(self.build(records), original)

    def test_insufficient_history_is_reported(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12]),
        }

        history = self.build(
            records,
            start="2026-01-01",
            end="2026-01-01",
        )

        self.assertEqual(history[0]["evaluated_count"], 0)
        self.assertEqual(history[0]["candidates"], [])
        self.assertIn("AAA", history[0]["errors"])

    def test_no_matching_dates(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12]),
        }

        history = self.build(
            records,
            start="2026-02-01",
            end="2026-02-02",
        )

        self.assertEqual(history, [])


if __name__ == "__main__":
    unittest.main()