import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.analysis.factors import build_factor_history
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import (
    run_portfolio_backtest,
)


class TestFactorHistory(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "short_window": 2,
            "long_window": 3,
            "momentum_window": 2,
            "volatility_window": 2,
            "top_n": 1,
            "momentum_weight": Decimal("1"),
            "trend_weight": Decimal("0"),
            "volatility_weight": Decimal("0"),
        }

        self.records = {
            "AAA": self.make_records("AAA", [10, 11, 12, 13]),
            "BBB": self.make_records("BBB", [10, 12, 14, 16]),
        }

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
                "volume": 100 * (index + 1),
            }
            for index, close in enumerate(closes)
        ]

    def build(self, start="2026-01-03", end="2026-01-04"):
        return build_factor_history(
            self.records,
            start,
            end,
            factor_settings=self.settings,
        )

    def test_daily_ranking_with_warmup(self):
        history = self.build()

        self.assertEqual(len(history), 2)

        for day in history:
            self.assertEqual(day["errors"], {})
            self.assertEqual(day["evaluated_count"], 2)
            self.assertEqual(
                day["candidates"][0]["stock_id"],
                "BBB",
            )

    def test_future_data_does_not_change_past(self):
        original = self.build()

        self.records["AAA"].append(
            {
                "stock_id": "AAA",
                "date": "2026-01-05",
                "open": Decimal("999"),
                "close": Decimal("999"),
                "volume": 999999,
            }
        )

        self.assertEqual(self.build(), original)

    def test_ranking_recovers_when_missing_close_leaves_window(self):
        expected = self.build()[-1]
        self.records["AAA"][0]["close"] = None
        history = self.build()
        self.assertIn("收盤價", history[0]["errors"]["AAA"])
        self.assertEqual(history[0]["candidates"], [])
        self.assertEqual(history[-1], expected)

    def test_missing_date_prevents_partial_ranking(self):
        self.records["BBB"].pop()

        history = self.build()
        last_day = history[-1]

        self.assertIn("BBB", last_day["errors"])
        self.assertEqual(last_day["candidates"], [])

    def test_warmup_errors_are_reported(self):
        history = self.build(
            start="2026-01-01",
            end="2026-01-01",
        )

        self.assertEqual(history[0]["candidates"], [])
        self.assertEqual(len(history[0]["errors"]), 2)

    def test_factor_ranking_drives_next_day_trade(self):
        result = run_portfolio_backtest(
            self.records,
            "2026-01-01",
            "2026-01-04",
            screening_settings={},
            initial_cash=Decimal("1000"),
            quantity=10,
            max_positions=1,
            cost_settings=CostSettings(
                commission_rate=Decimal("0"),
                commission_discount=Decimal("1"),
                minimum_commission=Decimal("0"),
                sell_tax_rate=Decimal("0"),
            ),
            ranking_method="factors",
            factor_settings=self.settings,
        )

        self.assertEqual(result["ranking_method"], "factors")
        self.assertEqual(len(result["trades"]), 1)

        trade = result["trades"][0]

        self.assertEqual(trade["stock_id"], "BBB")
        self.assertEqual(trade["signal_date"], "2026-01-03")
        self.assertEqual(trade["date"], "2026-01-04")
        self.assertEqual(trade["price"], Decimal("13"))

        # 買進 BBB 10 股：現金 870。
        # 期末收盤價 16，總資產 870 + 160 = 1030。
        self.assertEqual(result["holdings"], {"BBB": 10})
        self.assertEqual(result["final_equity"], Decimal("1030"))