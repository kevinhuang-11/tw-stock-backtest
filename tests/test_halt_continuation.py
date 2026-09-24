import unittest
from decimal import Decimal
from unittest.mock import patch

from tw_stock_backtest.backtesting import portfolio
from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.costs import CostSettings


class TestHaltContinuation(unittest.TestCase):
    def setUp(self):
        self.events = {
            ("AAA", "2026-01-03"): {
                "reason": "人工停牌案例",
                "source": "test fixture",
            }
        }

        self.records = {
            stock_id: [
                self.normal(stock_id, day)
                for day in range(1, 6)
            ]
            for stock_id in ("AAA", "BBB")
        }

        self.records["AAA"][2] = {
            "stock_id": "AAA",
            "date": "2026-01-03",
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": 0,
            "turnover": 0,
            "trade_count": 0,
        }

        self.costs = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

        self.settings = {
            "short_window": 2,
            "long_window": 3,
            "volume_window": 2,
            "min_volume_ratio": Decimal("1"),
            "momentum_window": 2,
            "top_n": 2,
        }

    def normal(self, stock_id, day):
        return {
            "stock_id": stock_id,
            "date": f"2026-01-0{day}",
            "open": Decimal("10"),
            "high": Decimal("10"),
            "low": Decimal("10"),
            "close": Decimal("10"),
            "volume": 100,
            "turnover": 1000,
            "trade_count": 10,
        }

    def run_engine(self, signals, *, cash="1000", max_positions=2):
        def fake_evaluate(records, as_of, **kwargs):
            stock_id = records[-1]["stock_id"]

            return {
                "stock_id": stock_id,
                "date": as_of,
                "selected": stock_id in signals.get(as_of, []),
                "momentum": Decimal("1"),
            }

        with patch.object(
            portfolio,
            "evaluate_stock",
            side_effect=fake_evaluate,
        ) as mock_evaluate:
            result = portfolio.run_portfolio_backtest(
                self.records,
                "2026-01-01",
                "2026-01-05",
                screening_settings=self.settings,
                initial_cash=Decimal(cash),
                quantity=10,
                max_positions=max_positions,
                cost_settings=self.costs,
                confirmed_halts=self.events,
            )

        return result, mock_evaluate

    def test_other_stock_trades_and_halted_stock_resumes(self):
        result, mock_evaluate = self.run_engine(
            {
                "2026-01-01": ["AAA"],
                "2026-01-02": ["BBB"],
                "2026-01-03": ["BBB"],
                "2026-01-04": ["BBB"],
                "2026-01-05": ["BBB"],
            }
        )

        actual = [
            (trade["stock_id"], trade["action"], trade["date"])
            for trade in result["trades"]
        ]

        self.assertEqual(
            actual,
            [
                ("AAA", "BUY", "2026-01-02"),
                ("BBB", "BUY", "2026-01-03"),
                ("AAA", "SELL", "2026-01-05"),
            ],
        )

        # 停牌日 AAA 賣不掉，但 BBB 仍能買進。
        self.assertEqual(
            result["equity_curve"][2]["holdings"],
            {"AAA": 10, "BBB": 10},
        )
        self.assertEqual(
            result["equity_curve"][2]["stale_prices"],
            {"AAA": "2026-01-02"},
        )

        # AAA 在恢復交易的 1/4 重新計算訊號。
        resumed_calls = [
            call
            for call in mock_evaluate.call_args_list
            if call.args[1] == "2026-01-04"
            and call.args[0][-1]["stock_id"] == "AAA"
        ]

        self.assertEqual(len(resumed_calls), 1)

        # 計算歷史中沒有把停牌日當成新價格。
        history_dates = [
            row["date"]
            for row in resumed_calls[0].args[0]
        ]
        self.assertNotIn("2026-01-03", history_dates)

    def test_failed_sell_does_not_release_cash(self):
        result, _ = self.run_engine(
            {
                "2026-01-01": ["AAA"],
                "2026-01-02": ["BBB"],
            },
            cash="150",
        )

        skipped = [
            order
            for order in result["skipped_orders"]
            if order["date"] == "2026-01-03"
            and order["stock_id"] == "BBB"
        ]

        self.assertEqual(len(skipped), 1)
        self.assertEqual(
            skipped[0]["reason"],
            "資金不足，含手續費",
        )
        self.assertEqual(
            result["equity_curve"][2]["cash"],
            Decimal("50"),
        )

    def test_halted_holding_still_uses_position_slot(self):
        result, _ = self.run_engine(
            {
                "2026-01-01": ["AAA"],
                "2026-01-02": ["BBB"],
            },
            max_positions=1,
        )

        self.assertTrue(
            any(
                order["date"] == "2026-01-03"
                and order["stock_id"] == "BBB"
                and order["reason"] == "已達持股上限"
                for order in result["skipped_orders"]
            )
        )

    def test_halted_buy_does_not_block_other_stock(self):
        result, _ = self.run_engine(
            {
                "2026-01-02": ["AAA", "BBB"],
                "2026-01-03": ["BBB"],
                "2026-01-04": ["AAA", "BBB"],
                "2026-01-05": ["AAA", "BBB"],
            }
        )

        actual = [
            (trade["stock_id"], trade["action"], trade["date"])
            for trade in result["trades"]
        ]

        self.assertEqual(
            actual,
            [
                ("BBB", "BUY", "2026-01-03"),
                ("AAA", "BUY", "2026-01-05"),
            ],
        )

    def test_benchmark_continues_through_halt(self):
        result = run_buy_and_hold(
            self.records,
            "2026-01-01",
            "2026-01-05",
            initial_cash=Decimal("1000"),
            cost_settings=self.costs,
            confirmed_halts=self.events,
        )

        self.assertEqual(len(result["equity_curve"]), 5)
        self.assertEqual(result["final_equity"], Decimal("1000"))
        self.assertEqual(
            result["equity_curve"][2]["stale_prices"],
            {"AAA": "2026-01-02"},
        )

    def test_benchmark_delays_only_the_halted_stock(self):
        result = run_buy_and_hold(
            self.records,
            "2026-01-03",
            "2026-01-05",
            initial_cash=Decimal("1000"),
            cost_settings=self.costs,
            confirmed_halts=self.events,
        )

        self.assertEqual(
            [
                (trade["stock_id"], trade["date"])
                for trade in result["trades"]
            ],
            [
                ("BBB", "2026-01-03"),
                ("AAA", "2026-01-04"),
            ],
        )


if __name__ == "__main__":
    unittest.main()