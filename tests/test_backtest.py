import unittest
from decimal import Decimal

from backtest import run_backtest


class TestBacktest(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "stock_id": "2330",
                "date": "2025-01-02",
                "open": Decimal("10"),
                "close": Decimal("11"),
            },
            {
                "stock_id": "2330",
                "date": "2025-01-03",
                "open": Decimal("12"),
                "close": Decimal("13"),
            },
            {
                "stock_id": "2330",
                "date": "2025-01-06",
                "open": Decimal("14"),
                "close": Decimal("15"),
            },
        ]

    def test_buy_and_sell_at_next_open(self):
        result = run_backtest(
            self.records,
            ["BUY", "SELL", "NONE"],
            Decimal("100"),
            2,
        )

        trades = result["trades"]

        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["signal_date"], "2025-01-02")
        self.assertEqual(trades[0]["date"], "2025-01-03")
        self.assertEqual(trades[0]["price"], Decimal("12"))
        self.assertEqual(trades[0]["quantity"], 2)
        self.assertEqual(trades[1]["date"], "2025-01-06")
        self.assertEqual(trades[1]["price"], Decimal("14"))

        # 100 - 12 × 2 + 14 × 2 = 104
        self.assertEqual(result["cash"], Decimal("104"))
        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["total_return"], Decimal("0.04"))

        # 買入當天收盤：76 現金＋13 × 2 股 = 102
        self.assertEqual(
            result["equity_curve"][1]["equity"],
            Decimal("102"),
        )

    def test_final_day_signal_is_not_executed(self):
        result = run_backtest(
            self.records,
            ["NONE", "NONE", "BUY"],
            Decimal("100"),
            2,
        )

        self.assertEqual(result["trades"], [])
        self.assertEqual(result["final_equity"], Decimal("100"))

    def test_sell_without_position_is_ignored(self):
        result = run_backtest(
            self.records,
            ["SELL", "NONE", "NONE"],
            Decimal("100"),
            2,
        )

        self.assertEqual(result["trades"], [])
        self.assertEqual(result["shares"], 0)

    def test_insufficient_cash(self):
        result = run_backtest(
            self.records,
            ["BUY", "NONE", "NONE"],
            Decimal("20"),
            2,
        )

        self.assertEqual(result["trades"], [])
        self.assertEqual(result["cash"], Decimal("20"))

    def test_no_additional_buy_while_holding(self):
        result = run_backtest(
            self.records,
            ["BUY", "BUY", "NONE"],
            Decimal("100"),
            2,
        )

        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["shares"], 2)

        # 期末不強制賣出：76 現金＋15 × 2 股 = 106
        self.assertEqual(result["cash"], Decimal("76"))
        self.assertEqual(result["final_equity"], Decimal("106"))

    def test_exact_cash_is_enough(self):
        result = run_backtest(
            self.records,
            ["BUY", "NONE", "NONE"],
            Decimal("24"),
            2,
        )

        self.assertEqual(result["cash"], Decimal("0"))
        self.assertEqual(result["shares"], 2)

    def test_reversed_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            run_backtest(
                self.records[::-1],
                ["NONE"] * 3,
                Decimal("100"),
                2,
            )

    def test_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            run_backtest(
                self.records,
                ["BUY"],
                Decimal("100"),
                2,
            )


if __name__ == "__main__":
    unittest.main()