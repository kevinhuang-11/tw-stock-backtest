import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.plotting import prepare_series


class TestPrepareSeries(unittest.TestCase):
    def make_result(self, equities):
        start = date(2026, 1, 1)

        return {
            "initial_cash": "100",
            "equity_curve": [
                {
                    "date": (
                        start + timedelta(days=index)
                    ).isoformat(),
                    "equity": str(equity),
                    "holdings": {},
                }
                for index, equity in enumerate(equities)
            ],
        }

    def test_drawdown_series(self):
        series = prepare_series(
            self.make_result([100, 120, 90, 120])
        )

        self.assertEqual(
            series["drawdowns"],
            [
                Decimal("0"),
                Decimal("0"),
                Decimal("-0.25"),
                Decimal("0"),
            ],
        )

        self.assertEqual(
            series["performance"]["max_drawdown"],
            Decimal("0.25"),
        )

    def test_first_day_loss_uses_initial_capital(self):
        series = prepare_series(
            self.make_result([90, 95])
        )

        self.assertEqual(
            series["drawdowns"],
            [
                Decimal("-0.10"),
                Decimal("-0.05"),
            ],
        )


if __name__ == "__main__":
    unittest.main()