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

class TestBenchmarkSlippage(unittest.TestCase):
    def setUp(self):
        self.cost_settings = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

    def make_records(self, stock_id, prices):
        return [
            {
                "stock_id": stock_id,
                "date": f"2024-01-{day:02d}",
                "open": Decimal(price),
                "high": Decimal(price),
                "low": Decimal(price),
                "close": Decimal(price),
                "volume": 100,
                "turnover": 1000,
                "trade_count": 10,
            }
            for day, price in enumerate(prices, start=1)
        ]

    def test_slippage_changes_buy_price_and_quantity(self):
        result = run_buy_and_hold(
            {
                "AAA": self.make_records("AAA", ["10", "10"]),
            },
            "2024-01-01",
            "2024-01-02",
            initial_cash=Decimal("1000"),
            cost_settings=self.cost_settings,
            slippage_rate=Decimal("0.01"),
        )

        # 買進價 10.1，買 99 股，剩餘現金 0.1。
        self.assertEqual(result["holdings"], {"AAA": 99})
        self.assertEqual(result["cash"], Decimal("0.1"))
        self.assertEqual(
            result["trades"][0]["raw_price"],
            Decimal("10"),
        )
        self.assertEqual(
            result["trades"][0]["price"],
            Decimal("10.1"),
        )

        # 收盤仍按 10 元估值，不強制賣出。
        self.assertEqual(result["final_equity"], Decimal("990.1"))
        self.assertEqual(len(result["trades"]), 1)

    def test_slippage_and_commission_share_same_budget(self):
        settings = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("20"),
            sell_tax_rate=Decimal("0"),
        )

        result = run_buy_and_hold(
            {
                "AAA": self.make_records("AAA", ["10", "10"]),
            },
            "2024-01-01",
            "2024-01-02",
            initial_cash=Decimal("1000"),
            cost_settings=settings,
            slippage_rate=Decimal("0.01"),
        )

        # 97 股 × 10.1 + 20 = 999.7。
        # 98 股 × 10.1 + 20 = 1009.8，超出預算。
        self.assertEqual(result["holdings"], {"AAA": 97})
        self.assertEqual(result["cash"], Decimal("0.3"))
        self.assertEqual(result["total_commission"], Decimal("20"))
        self.assertEqual(result["final_equity"], Decimal("970.3"))

    def test_delayed_buy_uses_resumption_open(self):
        records = self.make_records("AAA", ["10", "20"])

        for field in ("open", "high", "low", "close"):
            records[0][field] = None
        for field in ("volume", "turnover", "trade_count"):
            records[0][field] = 0

        result = run_buy_and_hold(
            {"AAA": records},
            "2024-01-01",
            "2024-01-02",
            initial_cash=Decimal("1000"),
            cost_settings=self.cost_settings,
            confirmed_halts={
                ("AAA", "2024-01-01"): {
                    "reason": "人工停牌測試",
                    "source": "unit test",
                },
            },
            slippage_rate=Decimal("0.01"),
        )

        # 復牌開盤價 20，加滑價後為 20.2。
        # 49 股花費 989.8，剩餘 10.2。
        self.assertEqual(result["holdings"], {"AAA": 49})
        self.assertEqual(result["cash"], Decimal("10.2"))
        self.assertEqual(
            result["trades"][0]["date"],
            "2024-01-02",
        )
        self.assertEqual(
            result["trades"][0]["price"],
            Decimal("20.2"),
        )

    def test_invalid_slippage_is_rejected(self):
        with self.assertRaises(ValueError):
            run_buy_and_hold(
                {
                    "AAA": self.make_records("AAA", ["10", "10"]),
                },
                "2024-01-01",
                "2024-01-02",
                initial_cash=Decimal("1000"),
                cost_settings=self.cost_settings,
                slippage_rate=Decimal("-0.01"),
            )

            
if __name__ == "__main__":
    unittest.main()