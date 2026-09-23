import unittest
from decimal import Decimal

from tw_stock_backtest.backtesting.benchmark import (
    run_buy_and_hold,
)
from tw_stock_backtest.backtesting.costs import CostSettings


class TestBuyAndHold(unittest.TestCase):
    def setUp(self):
        self.records = {
            "AAA": [
                {
                    "stock_id": "AAA",
                    "date": "2026-01-05",
                    "open": Decimal("10"),
                    "close": Decimal("11"),
                },
                {
                    "stock_id": "AAA",
                    "date": "2026-01-06",
                    "open": Decimal("11"),
                    "close": Decimal("12"),
                },
            ],
            "BBB": [
                {
                    "stock_id": "BBB",
                    "date": "2026-01-05",
                    "open": Decimal("20"),
                    "close": Decimal("21"),
                },
                {
                    "stock_id": "BBB",
                    "date": "2026-01-06",
                    "open": Decimal("21"),
                    "close": Decimal("22"),
                },
            ],
        }

    def run_benchmark(self, *, fee="0", minimum="0", cash="1000"):
        return run_buy_and_hold(
            self.records,
            "2026-01-05",
            "2026-01-06",
            initial_cash=Decimal(cash),
            cost_settings=CostSettings(
                commission_rate=Decimal(fee),
                commission_discount=Decimal("1"),
                minimum_commission=Decimal(minimum),
                sell_tax_rate=Decimal("0.003"),
            ),
        )

    def test_equal_budget_without_costs(self):
        result = self.run_benchmark()

        # 每檔預算 500：AAA 買 50 股，BBB 買 25 股。
        self.assertEqual(
            result["holdings"],
            {"AAA": 50, "BBB": 25},
        )
        self.assertEqual(result["cash"], Decimal("0"))

        # 50 × 12 + 25 × 22 = 1150。
        self.assertEqual(result["final_equity"], Decimal("1150"))
        self.assertEqual(result["total_return"], Decimal("0.15"))

        # 只在第一天買進，不在期末賣出。
        self.assertEqual(len(result["trades"]), 2)
        self.assertTrue(
            all(
                trade["date"] == "2026-01-05"
                and trade["action"] == "BUY"
                for trade in result["trades"]
            )
        )
        self.assertEqual(result["total_tax"], Decimal("0"))

    def test_budget_includes_commission(self):
        result = self.run_benchmark(fee="0.01")

        # AAA：49 × 10 + 4 = 494。
        # BBB：24 × 20 + 4 = 484。
        self.assertEqual(
            result["holdings"],
            {"AAA": 49, "BBB": 24},
        )
        self.assertEqual(result["cash"], Decimal("22"))
        self.assertEqual(result["total_commission"], Decimal("8"))

        # 22 + 49 × 12 + 24 × 22 = 1138。
        self.assertEqual(result["final_equity"], Decimal("1138"))

    def test_cannot_afford_one_share_with_minimum_fee(self):
        result = self.run_benchmark(
            minimum="20",
            cash="20",
        )

        self.assertEqual(result["holdings"], {})
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["unbought"], ["AAA", "BBB"])
        self.assertEqual(result["final_equity"], Decimal("20"))

    def test_missing_date_is_rejected(self):
        self.records["BBB"].pop()

        with self.assertRaises(ValueError):
            self.run_benchmark()


if __name__ == "__main__":
    unittest.main()