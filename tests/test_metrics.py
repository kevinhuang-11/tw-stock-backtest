import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.backtesting.metrics import (
    summarize_performance, summarize_exposure
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

class TestExposure(unittest.TestCase):
    def test_average_includes_cash_only_days(self):
        curve = [
            {
                "equity": Decimal("1000"),
                "market_value": Decimal("0"),
            },
            {
                "equity": Decimal("1000"),
                "market_value": Decimal("500"),
            },
            {
                "equity": Decimal("2000"),
                "market_value": Decimal("2000"),
            },
        ]

        result = summarize_exposure(curve)

        # 每日占比：0%、50%、100%。
        # 平均：(0 + 0.5 + 1) / 3 = 0.5。
        self.assertEqual(
            result["average_exposure"],
            Decimal("0.5"),
        )
        self.assertEqual(
            result["max_exposure"],
            Decimal("1"),
        )

    def test_all_cash(self):
        curve = [
            {
                "equity": Decimal("1000"),
                "market_value": Decimal("0"),
            },
        ]

        result = summarize_exposure(curve)

        self.assertEqual(
            result["average_exposure"],
            Decimal("0"),
        )
        self.assertEqual(
            result["max_exposure"],
            Decimal("0"),
        )

    def test_zero_equity(self):
        curve = [
            {
                "equity": Decimal("0"),
                "market_value": Decimal("0"),
            },
        ]

        result = summarize_exposure(curve)

        # 本程式約定：總資產與持股市值皆為零時，占比為零。
        self.assertEqual(
            result["average_exposure"],
            Decimal("0"),
        )
        self.assertEqual(
            result["max_exposure"],
            Decimal("0"),
        )

    def test_invalid_input(self):
        invalid_curves = [
            # 沒有資料。
            [],

            # 缺少持股市值。
            [
                {
                    "equity": Decimal("1000"),
                },
            ],

            # 總資產不是有限數值。
            [
                {
                    "equity": Decimal("NaN"),
                    "market_value": Decimal("0"),
                },
            ],

            # 持股市值不是有限數值。
            [
                {
                    "equity": Decimal("1000"),
                    "market_value": Decimal("Infinity"),
                },
            ],

            # 總資產為負。
            [
                {
                    "equity": Decimal("-1"),
                    "market_value": Decimal("0"),
                },
            ],

            # 持股市值為負。
            [
                {
                    "equity": Decimal("1000"),
                    "market_value": Decimal("-1"),
                },
            ],

            # 本專案不融資，持股市值不可超過總資產。
            [
                {
                    "equity": Decimal("1000"),
                    "market_value": Decimal("1001"),
                },
            ],

            # 型別錯誤：應使用 Decimal。
            [
                {
                    "equity": 1000,
                    "market_value": Decimal("0"),
                },
            ],
        ]

        for curve in invalid_curves:
            with self.subTest(curve=curve):
                with self.assertRaises(ValueError):
                    summarize_exposure(curve)


if __name__ == "__main__":
    unittest.main()