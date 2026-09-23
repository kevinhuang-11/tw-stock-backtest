import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.backtesting.metrics import (
    summarize_performance,
)


class TestPerformanceMetrics(unittest.TestCase):
    def make_curve(self, values, invested_indices=()):
        start = date(2026, 1, 1)

        return [
            {
                "date": (
                    start + timedelta(days=index)
                ).isoformat(),
                "equity": Decimal(str(value)),
                "holdings": (
                    {"AAA": 100}
                    if index in invested_indices
                    else {}
                ),
            }
            for index, value in enumerate(values)
        ]

    def test_known_performance(self):
        curve = self.make_curve(
            [100, 120, 90, 110],
            invested_indices=(1, 2, 3),
        )

        result = summarize_performance(
            curve,
            Decimal("100"),
        )

        self.assertEqual(result["total_return"], Decimal("0.1"))
        self.assertEqual(result["max_drawdown"], Decimal("0.25"))
        self.assertEqual(result["highest_equity"], Decimal("120"))
        self.assertEqual(result["highest_date"], "2026-01-02")
        self.assertEqual(result["lowest_equity"], Decimal("90"))
        self.assertEqual(result["lowest_date"], "2026-01-03")
        self.assertEqual(result["total_days"], 4)
        self.assertEqual(result["invested_days"], 3)
        self.assertEqual(result["cash_only_days"], 1)
        self.assertEqual(
            result["invested_day_ratio"],
            Decimal("0.75"),
        )

    def test_first_day_loss_counts_as_drawdown(self):
        result = summarize_performance(
            self.make_curve([90, 95]),
            Decimal("100"),
        )

        self.assertEqual(
            result["max_drawdown"],
            Decimal("0.1"),
        )

    def test_drawdown_respects_time_order(self):
        result = summarize_performance(
            self.make_curve([80, 100, 120]),
            Decimal("100"),
        )

        # 80 發生在 120 之前。
        # 不能用後來的最高值 120 回頭計算早先的跌幅。
        self.assertEqual(
            result["max_drawdown"],
            Decimal("0.2"),
        )

    def test_rising_equity_has_no_drawdown(self):
        result = summarize_performance(
            self.make_curve([100, 110, 120]),
            Decimal("100"),
        )

        self.assertEqual(result["max_drawdown"], Decimal("0"))
        self.assertEqual(result["cash_only_days"], 3)

    def test_empty_curve_is_rejected(self):
        with self.assertRaises(ValueError):
            summarize_performance([], Decimal("100"))

    def test_invalid_records_are_rejected(self):
        with self.subTest(case="日期順序錯誤"):
            with self.assertRaises(ValueError):
                summarize_performance(
                    self.make_curve([100, 110])[::-1],
                    Decimal("100"),
                )

        for value in ("NaN", "Infinity", "-1"):
            with self.subTest(equity=value):
                with self.assertRaises(ValueError):
                    summarize_performance(
                        self.make_curve([value]),
                        Decimal("100"),
                    )


if __name__ == "__main__":
    unittest.main()