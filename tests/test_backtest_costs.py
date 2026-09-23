import unittest
from decimal import Decimal

from tw_stock_backtest.backtesting.backtest import run_backtest
from tw_stock_backtest.backtesting.costs import CostSettings


class TestBacktestCosts(unittest.TestCase):
    def setUp(self):
        self.settings = CostSettings()

        self.records = [
            {
                "stock_id": "2330",
                "date": "2025-01-02",
                "open": Decimal("90"),
                "close": Decimal("95"),
            },
            {
                "stock_id": "2330",
                "date": "2025-01-03",
                "open": Decimal("100"),
                "close": Decimal("105"),
            },
            {
                "stock_id": "2330",
                "date": "2025-01-06",
                "open": Decimal("110"),
                "close": Decimal("115"),
            },
        ]

    def test_round_trip_deducts_costs(self):
        result = run_backtest(
            self.records,
            ["BUY", "SELL", "NONE"],
            Decimal("200000"),
            1000,
            cost_settings=self.settings,
        )

        # 買入：100000 元股款＋142 元手續費。
        # 賣出：110000 元股款－156 元手續費－330 元稅。
        # 200000 - 100000 - 142 + 110000 - 156 - 330 = 209372
        self.assertEqual(result["cash"], Decimal("209372"))
        self.assertEqual(result["final_equity"], Decimal("209372"))
        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["total_commission"], Decimal("298"))
        self.assertEqual(result["total_tax"], Decimal("330"))

        trades = result["trades"]
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["cash_change"], Decimal("-100142"))
        self.assertEqual(trades[1]["cash_change"], Decimal("109514"))

    def test_cash_must_cover_commission(self):
        result = run_backtest(
            self.records,
            ["BUY", "NONE", "NONE"],
            Decimal("100000"),
            1000,
            cost_settings=self.settings,
        )

        # 股款夠，但不夠支付手續費，因此不能成交。
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["cash"], Decimal("100000"))
        self.assertEqual(result["total_commission"], Decimal("0"))
        self.assertEqual(result["total_tax"], Decimal("0"))

    def test_exact_cash_including_commission(self):
        result = run_backtest(
            self.records,
            ["BUY", "NONE", "NONE"],
            Decimal("100142"),
            1000,
            cost_settings=self.settings,
        )

        self.assertEqual(result["cash"], Decimal("0"))
        self.assertEqual(result["shares"], 1000)
        self.assertEqual(result["total_commission"], Decimal("142"))

        # 期末仍持股，以 115 元估值，不扣未發生的賣出費稅。
        self.assertEqual(result["final_equity"], Decimal("115000"))
        self.assertEqual(result["total_tax"], Decimal("0"))

    def test_no_execution_has_no_costs(self):
        result = run_backtest(
            self.records,
            ["SELL", "NONE", "BUY"],
            Decimal("200000"),
            1000,
            cost_settings=self.settings,
        )

        # 空手 SELL 不成交，最後一天 BUY 也不成交。
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["total_commission"], Decimal("0"))
        self.assertEqual(result["total_tax"], Decimal("0"))
        self.assertEqual(result["cash"], Decimal("200000"))


if __name__ == "__main__":
    unittest.main()