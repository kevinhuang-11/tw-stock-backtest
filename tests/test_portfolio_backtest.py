import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import (
    run_portfolio_backtest,
)


class TestPortfolioBacktest(unittest.TestCase):
    def setUp(self):
        self.screening_settings = {
            "short_window": 2,
            "long_window": 3,
            "volume_window": 1,
            "min_volume_ratio": Decimal("1"),
            "momentum_window": 1,
            "top_n": 2,
        }

        # 人工費率，只為方便驗算。
        self.cost_settings = CostSettings(
            commission_rate=Decimal("0.01"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0.01"),
        )

    def make_records(self, stock_id, closes):
        start = date(2026, 1, 1)

        return [
            {
                "stock_id": stock_id,
                "date": (
                    start + timedelta(days=index)
                ).isoformat(),
                "open": Decimal(10 + index),
                "close": Decimal(str(close)),
                "volume": 100 * (2 ** index),
            }
            for index, close in enumerate(closes)
        ]

    def run_engine(
        self,
        records,
        *,
        cash="1000",
        end="2026-01-04",
        max_positions=2,
    ):
        return run_portfolio_backtest(
            records,
            "2026-01-01",
            end,
            screening_settings=self.screening_settings,
            initial_cash=Decimal(cash),
            quantity=10,
            max_positions=max_positions,
            cost_settings=self.cost_settings,
        )

    def test_signal_executes_next_day_with_costs(self):
        result = self.run_engine(
            {"AAA": self.make_records("AAA", [10, 11, 12, 13])}
        )

        # 1/3：Close 12 > SMA2 11.5 > SMA3 11。
        # 1/4：以開盤價 13 買進。
        self.assertEqual(len(result["trades"]), 1)

        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], "2026-01-03")
        self.assertEqual(trade["date"], "2026-01-04")
        self.assertEqual(trade["action"], "BUY")
        self.assertEqual(trade["price"], Decimal("13"))
        self.assertEqual(trade["commission"], Decimal("1"))

        # 買進支出：13 × 10 + 1 = 131。
        self.assertEqual(result["cash"], Decimal("869"))
        self.assertEqual(result["holdings"], {"AAA": 10})

        # 期末總資產：869 + 13 × 10 = 999。
        self.assertEqual(result["final_equity"], Decimal("999"))

        # 後來買進不應改變先前的持股快照。
        self.assertEqual(
            result["equity_curve"][0]["holdings"],
            {},
        )

    def test_exit_executes_next_day_with_sell_tax(self):
        result = self.run_engine(
            {
                "AAA": self.make_records(
                    "AAA",
                    [10, 11, 12, 9, 8],
                )
            },
            end="2026-01-05",
        )

        self.assertEqual(
            [trade["action"] for trade in result["trades"]],
            ["BUY", "SELL"],
        )

        buy, sell = result["trades"]

        self.assertEqual(buy["signal_date"], "2026-01-03")
        self.assertEqual(buy["date"], "2026-01-04")

        # 1/4 收盤跌到 9，不再符合條件。
        # 1/5 以人工設定的開盤價 14 賣出。
        self.assertEqual(sell["signal_date"], "2026-01-04")
        self.assertEqual(sell["date"], "2026-01-05")
        self.assertEqual(sell["price"], Decimal("14"))

        # 買進支出：130 + 1 = 131。
        # 賣出收入：140 - 1 - 1 = 138。
        # 期末現金：1000 - 131 + 138 = 1007。
        self.assertEqual(result["cash"], Decimal("1007"))
        self.assertEqual(result["holdings"], {})
        self.assertEqual(result["total_commission"], Decimal("2"))
        self.assertEqual(result["total_tax"], Decimal("1"))

    def test_insufficient_cash_includes_commission(self):
        result = self.run_engine(
            {"AAA": self.make_records("AAA", [10, 11, 12, 13])},
            cash="130",
        )

        # 股票價款為 130，但加上手續費需要 131。
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["cash"], Decimal("130"))
        self.assertEqual(len(result["skipped_orders"]), 1)
        self.assertEqual(
            result["skipped_orders"][0]["reason"],
            "資金不足，含手續費",
        )

    def test_final_day_signal_does_not_execute(self):
        result = self.run_engine(
            {"AAA": self.make_records("AAA", [10, 11, 12])},
            end="2026-01-03",
        )

        # 最後一天才符合選股條件，沒有下一天可以成交。
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["final_equity"], Decimal("1000"))

        # 確認最後一天有成功評估，而非因評估錯誤而沒有交易。
        self.assertNotIn(
            "2026-01-03",
            [
                item["date"]
                for item in result["skipped_rebalances"]
            ],
        )

    def test_shared_cash_is_used_in_rank_order(self):
        result = self.run_engine(
            {
                "AAA": self.make_records("AAA", [10, 11, 12, 13]),
                "BBB": self.make_records("BBB", [10, 12, 14, 16]),
            },
            cash="200",
        )

        # 1/3，BBB 的一期價格報酬率較高，排在前面。
        # 1/4，每筆買進需 131 元，買 BBB 後只剩 69 元。
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["stock_id"], "BBB")
        self.assertEqual(result["holdings"], {"BBB": 10})
        self.assertEqual(result["cash"], Decimal("69"))
        self.assertEqual(
            result["skipped_orders"][0]["stock_id"],
            "AAA",
        )

    def test_missing_daily_record_is_rejected(self):
        records = {
            "AAA": self.make_records("AAA", [10, 11, 12, 13]),
            "BBB": self.make_records("BBB", [10, 12, 14]),
        }

        with self.assertRaises(ValueError):
            self.run_engine(records)

class TestPortfolioBudget(unittest.TestCase):
    def setUp(self):
        self.cost_settings = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

        self.screening_settings = {
            "short_window": 1,
            "long_window": 2,
            "volume_window": 1,
            "min_volume_ratio": Decimal("0"),
            "momentum_window": 1,
            "top_n": 2,
        }

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

    def run_case(
        self,
        records,
        signals,
        *,
        initial_cash=Decimal("1000"),
        max_positions=2,
        sizing_mode="fixed_budget",
        cost_settings=None,
        confirmed_halts=None,
    ):
        def fake_evaluate(history, as_of, **kwargs):
            stock_id = history[-1]["stock_id"]

            return {
                "stock_id": stock_id,
                "selected": stock_id in signals.get(as_of, []),
                "momentum": Decimal("1"),
            }

        with patch(
            "tw_stock_backtest.backtesting.portfolio.evaluate_stock",
            side_effect=fake_evaluate,
        ):
            return run_portfolio_backtest(
                records,
                "2024-01-01",
                "2024-01-03",
                screening_settings=self.screening_settings,
                initial_cash=initial_cash,
                quantity=10,
                max_positions=max_positions,
                cost_settings=(
                    self.cost_settings
                    if cost_settings is None
                    else cost_settings
                ),
                sizing_mode=sizing_mode,
                confirmed_halts=(
                    {}
                    if confirmed_halts is None
                    else confirmed_halts
                ),
            )

    def test_equal_budget_and_no_daily_resizing(self):
        records = {
            "AAA": self.make_records("AAA", ["10", "10", "20"]),
            "BBB": self.make_records("BBB", ["20", "20", "20"]),
        }
        signals = {
            "2024-01-01": ["AAA", "BBB"],
            "2024-01-02": ["AAA", "BBB"],
            "2024-01-03": ["AAA", "BBB"],
        }

        result = self.run_case(records, signals)

        # 每檔預算 500 元：
        # AAA 買 50 股，BBB 買 25 股。
        self.assertEqual(
            result["holdings"],
            {"AAA": 50, "BBB": 25},
        )
        self.assertEqual(result["cash"], Decimal("0"))
        self.assertEqual(result["budget_per_position"], Decimal("500"))
        self.assertEqual(len(result["trades"]), 2)

        # 第一日產生訊號，第二日成交。
        self.assertTrue(
            all(
                trade["date"] == "2024-01-02"
                for trade in result["trades"]
            )
        )

        # 第三日 AAA 漲價，但不重新調整股數。
        self.assertEqual(result["final_equity"], Decimal("1500"))

    def test_budget_includes_commission(self):
        settings = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("20"),
            sell_tax_rate=Decimal("0"),
        )
        records = {
            "AAA": self.make_records("AAA", ["100", "100", "100"]),
        }
        signals = {
            "2024-01-01": ["AAA"],
            "2024-01-02": ["AAA"],
        }

        result = self.run_case(
            records,
            signals,
            max_positions=1,
            cost_settings=settings,
        )

        # 9 股 × 100 + 20 手續費 = 920。
        self.assertEqual(result["holdings"], {"AAA": 9})
        self.assertEqual(result["cash"], Decimal("80"))
        self.assertEqual(result["total_commission"], Decimal("20"))

    def test_replacement_uses_only_available_cash(self):
        records = {
            "AAA": self.make_records("AAA", ["10", "10", "5"]),
            "BBB": self.make_records("BBB", ["20", "20", "20"]),
        }
        signals = {
            "2024-01-01": ["AAA"],
            "2024-01-02": ["BBB"],
        }

        result = self.run_case(
            records,
            signals,
            max_positions=1,
        )

        # 第二日以 1000 元買進 AAA 100 股。
        # 第三日先以 5 元賣出，現金只剩 500 元。
        # BBB 只能買 25 股，不能使用原本 1000 元預算超支。
        self.assertEqual(result["holdings"], {"BBB": 25})
        self.assertEqual(result["cash"], Decimal("0"))
        self.assertEqual(
            [
                (trade["stock_id"], trade["action"])
                for trade in result["trades"]
            ],
            [
                ("AAA", "BUY"),
                ("AAA", "SELL"),
                ("BBB", "BUY"),
            ],
        )

    def test_unaffordable_stock_does_not_block_other_stock(self):
        records = {
            "AAA": self.make_records("AAA", ["600", "600", "600"]),
            "BBB": self.make_records("BBB", ["20", "20", "20"]),
        }

        result = self.run_case(
            records,
            {"2024-01-01": ["AAA", "BBB"]},
        )

        # 單檔預算 500，AAA 連一股都買不起。
        # BBB 仍正常買進；第三日依空候選名單賣出。
        self.assertEqual(
            [
                (trade["stock_id"], trade["action"])
                for trade in result["trades"]
            ],
            [("BBB", "BUY"), ("BBB", "SELL")],
        )
        self.assertEqual(result["skipped_orders"][0]["stock_id"], "AAA")
        self.assertIn(
            "不足以買進一股",
            result["skipped_orders"][0]["reason"],
        )
        self.assertEqual(result["cash"], Decimal("1000"))

    def test_halted_sell_does_not_release_slot_or_cash(self):
        records = {
            "AAA": self.make_records("AAA", ["10", "10", "10"]),
            "BBB": self.make_records("BBB", ["20", "20", "20"]),
        }

        halt = records["AAA"][2]
        for field in ("open", "high", "low", "close"):
            halt[field] = None
        for field in ("volume", "turnover", "trade_count"):
            halt[field] = 0

        result = self.run_case(
            records,
            {
                "2024-01-01": ["AAA"],
                "2024-01-02": ["BBB"],
            },
            max_positions=1,
            confirmed_halts={
                ("AAA", "2024-01-03"): {
                    "reason": "人工停牌測試",
                    "source": "unit test",
                },
            },
        )

        self.assertEqual(result["holdings"], {"AAA": 100})
        self.assertEqual(result["cash"], Decimal("0"))
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(len(result["skipped_orders"]), 2)
        self.assertEqual(
            result["skipped_orders"][1]["reason"],
            "已達持股上限",
        )

    def test_invalid_sizing_mode(self):
        records = {
            "AAA": self.make_records("AAA", ["10", "10", "10"]),
        }

        with self.assertRaises(ValueError):
            self.run_case(
                records,
                {},
                sizing_mode="unknown",
            )

if __name__ == "__main__":
    unittest.main()