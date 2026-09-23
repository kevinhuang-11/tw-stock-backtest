import unittest

from tw_stock_backtest.backtesting.portfolio import plan_rebalance


class TestPlanRebalance(unittest.TestCase):
    def test_empty_portfolio_buys_top_candidates(self):
        result = plan_rebalance(
            {},
            ["2454", "2330", "2317"],
            max_positions=2,
        )

        self.assertEqual(
            result,
            {
                "target": ["2454", "2330"],
                "sell": [],
                "buy": ["2454", "2330"],
                "keep": [],
            },
        )

    def test_replace_stock_outside_target(self):
        holdings = {"2330": 100, "2317": 100}
        ranked = ["2454", "2330"]

        result = plan_rebalance(
            holdings,
            ranked,
            max_positions=2,
        )

        self.assertEqual(result["sell"], ["2317"])
        self.assertEqual(result["buy"], ["2454"])
        self.assertEqual(result["keep"], ["2330"])

        # 產生計畫不應修改原本的持股或排名。
        self.assertEqual(
            holdings,
            {"2330": 100, "2317": 100},
        )
        self.assertEqual(ranked, ["2454", "2330"])

    def test_existing_target_is_not_bought_again(self):
        result = plan_rebalance(
            {"2330": 100},
            ["2330"],
            max_positions=2,
        )

        self.assertEqual(result["buy"], [])
        self.assertEqual(result["sell"], [])
        self.assertEqual(result["keep"], ["2330"])

    def test_empty_candidates_plan_to_sell_all(self):
        result = plan_rebalance(
            {"2330": 100, "2317": 100},
            [],
            max_positions=2,
        )

        self.assertEqual(result["target"], [])
        self.assertEqual(result["sell"], ["2317", "2330"])
        self.assertEqual(result["buy"], [])
        self.assertEqual(result["keep"], [])

    def test_lower_ranked_holding_is_removed(self):
        result = plan_rebalance(
            {"2317": 100},
            ["2454", "2330", "2317"],
            max_positions=2,
        )

        self.assertEqual(result["sell"], ["2317"])
        self.assertEqual(result["buy"], ["2454", "2330"])

    def test_duplicate_candidates_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_rebalance(
                {},
                ["2330", "2330"],
                max_positions=2,
            )

    def test_invalid_position_limits(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    plan_rebalance(
                        {},
                        ["2330"],
                        max_positions=value,
                    )

    def test_invalid_holdings(self):
        for quantity in (0, -100, True, 1.5):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    plan_rebalance(
                        {"2330": quantity},
                        ["2330"],
                        max_positions=2,
                    )


if __name__ == "__main__":
    unittest.main()