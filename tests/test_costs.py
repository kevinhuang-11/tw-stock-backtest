import unittest
from decimal import Decimal, ROUND_HALF_UP

from tw_stock_backtest.backtesting.costs import CostSettings, calculate_transaction
from tw_stock_backtest.backtesting.costs import calculate_buy_quantity
from tw_stock_backtest.backtesting.costs import apply_slippage


class TestTransactionCosts(unittest.TestCase):
    def setUp(self):
        self.settings = CostSettings()

    def test_buy_costs(self):
        result = calculate_transaction(
            Decimal("100"),
            1000,
            "BUY",
            self.settings,
        )

        # 股款 100000；手續費 142.5 捨去為 142。
        self.assertEqual(result["amount"], Decimal("100000"))
        self.assertEqual(result["commission"], Decimal("142"))
        self.assertEqual(result["tax"], Decimal("0"))
        self.assertEqual(result["cash_change"], Decimal("-100142"))

    def test_sell_costs(self):
        result = calculate_transaction(
            Decimal("100"),
            1000,
            "SELL",
            self.settings,
        )

        # 賣出淨收入 = 100000 - 142 - 300。
        self.assertEqual(result["commission"], Decimal("142"))
        self.assertEqual(result["tax"], Decimal("300"))
        self.assertEqual(result["cash_change"], Decimal("99558"))

    def test_minimum_commission(self):
        result = calculate_transaction(
            Decimal("10"),
            100,
            "BUY",
            self.settings,
        )

        # 原始手續費 1.425 元，套用最低 20 元。
        self.assertEqual(result["commission"], Decimal("20"))
        self.assertEqual(result["cash_change"], Decimal("-1020"))

    def test_discounted_commission(self):
        settings = CostSettings(
            commission_discount=Decimal("0.6"),
        )

        result = calculate_transaction(
            Decimal("100"),
            1000,
            "BUY",
            settings,
        )

        # 100000 × 0.001425 × 0.6 = 85.5，捨去為 85。
        self.assertEqual(result["commission"], Decimal("85"))

    def test_rounding_can_be_changed(self):
        settings = CostSettings(
            minimum_commission=Decimal("0"),
            commission_rounding=ROUND_HALF_UP,
            tax_rounding=ROUND_HALF_UP,
        )

        result = calculate_transaction(
            Decimal("100"),
            1000,
            "SELL",
            settings,
        )

        self.assertEqual(result["commission"], Decimal("143"))

        small = calculate_transaction(
            Decimal("10"),
            50,
            "SELL",
            settings,
        )

        # 500 × 0.003 = 1.5，四捨五入為 2。
        self.assertEqual(small["tax"], Decimal("2"))

    def test_zero_cost_settings(self):
        settings = CostSettings(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

        result = calculate_transaction(
            Decimal("100"),
            10,
            "SELL",
            settings,
        )

        self.assertEqual(result["commission"], Decimal("0"))
        self.assertEqual(result["tax"], Decimal("0"))
        self.assertEqual(result["cash_change"], Decimal("1000"))

    def test_invalid_trade_inputs(self):
        cases = [
            (Decimal("0"), 100, "BUY"),
            (Decimal("NaN"), 100, "BUY"),
            (Decimal("100"), 0, "BUY"),
            (Decimal("100"), True, "BUY"),
            (Decimal("100"), 100, "HOLD"),
        ]

        for price, quantity, side in cases:
            with self.subTest(price=price, quantity=quantity, side=side):
                with self.assertRaises(ValueError):
                    calculate_transaction(
                        price,
                        quantity,
                        side,
                        self.settings,
                    )

    def test_invalid_settings(self):
        cases = [
            {"commission_rate": Decimal("-0.01")},
            {"commission_discount": Decimal("1.1")},
            {"minimum_commission": Decimal("-1")},
            {"minimum_commission": Decimal("1.5")},
            {"sell_tax_rate": Decimal("NaN")},
            {"tax_rounding": "INVALID"},
        ]

        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    CostSettings(**values)

class TestBuyQuantity(unittest.TestCase):
    def setUp(self):
        self.settings = CostSettings(
            commission_rate=Decimal("0.001425"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("20"),
            sell_tax_rate=Decimal("0.003"),
            commission_rounding="ROUND_DOWN",
            tax_rounding="ROUND_DOWN",
        )

    def test_budget_includes_minimum_commission(self):
        quantity = calculate_buy_quantity(
            Decimal("100"),
            Decimal("1000"),
            self.settings,
        )

        # 9 股：900 + 20 = 920，可以買。
        # 10 股：1000 + 20 = 1020，超出預算。
        self.assertEqual(quantity, 9)

    def test_exact_budget_is_enough(self):
        quantity = calculate_buy_quantity(
            Decimal("100"),
            Decimal("1020"),
            self.settings,
        )

        # 10 股：1000 + 20 = 1020，剛好足夠。
        self.assertEqual(quantity, 10)

    def test_percentage_commission(self):
        quantity = calculate_buy_quantity(
            Decimal("1000"),
            Decimal("100000"),
            self.settings,
        )

        # 99 股：
        # 成交金額 99000，手續費 floor(141.075) = 141。
        # 合計 99141，可以買。
        #
        # 100 股：
        # 成交金額 100000，手續費 floor(142.5) = 142。
        # 合計 100142，超出預算。
        self.assertEqual(quantity, 99)

    def test_insufficient_budget_returns_zero(self):
        for budget in (Decimal("0"), Decimal("119")):
            with self.subTest(budget=budget):
                quantity = calculate_buy_quantity(
                    Decimal("100"),
                    budget,
                    self.settings,
                )

                # 1 股至少需要 100 + 20 = 120 元。
                self.assertEqual(quantity, 0)

    def test_zero_commission(self):
        settings = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

        quantity = calculate_buy_quantity(
            Decimal("100"),
            Decimal("1000"),
            settings,
        )

        self.assertEqual(quantity, 10)

    def test_invalid_inputs(self):
        cases = [
            (Decimal("0"), Decimal("1000")),
            (Decimal("-1"), Decimal("1000")),
            (Decimal("NaN"), Decimal("1000")),
            (Decimal("Infinity"), Decimal("1000")),
            (Decimal("100"), Decimal("-1")),
            (Decimal("100"), Decimal("NaN")),
            (Decimal("100"), Decimal("Infinity")),
            (100, Decimal("1000")),
            (Decimal("100"), 1000),
        ]

        for price, budget in cases:
            with self.subTest(price=price, budget=budget):
                with self.assertRaises(ValueError):
                    calculate_buy_quantity(
                        price,
                        budget,
                        self.settings,
                    )
class TestSlippage(unittest.TestCase):
    def test_buy_price_increases(self):
        result = apply_slippage(
            Decimal("100"),
            "BUY",
            Decimal("0.001"),
        )

        self.assertEqual(result, Decimal("100.1"))

    def test_sell_price_decreases(self):
        result = apply_slippage(
            Decimal("100"),
            "SELL",
            Decimal("0.001"),
        )

        self.assertEqual(result, Decimal("99.9"))

    def test_zero_slippage_preserves_price(self):
        for action in ("BUY", "SELL"):
            with self.subTest(action=action):
                result = apply_slippage(
                    Decimal("123.45"),
                    action,
                )

                self.assertEqual(result, Decimal("123.45"))

    def test_precision_is_preserved(self):
        result = apply_slippage(
            Decimal("123.45"),
            "BUY",
            Decimal("0.001"),
        )

        # 不先取成 123.57，避免過早捨入。
        self.assertEqual(result, Decimal("123.57345"))

    def test_invalid_inputs(self):
        cases = [
            (Decimal("0"), "BUY", Decimal("0")),
            (Decimal("-1"), "BUY", Decimal("0")),
            (Decimal("NaN"), "BUY", Decimal("0")),
            (Decimal("Infinity"), "BUY", Decimal("0")),
            (100, "BUY", Decimal("0")),
            (Decimal("100"), "HOLD", Decimal("0")),
            (Decimal("100"), "BUY", Decimal("-0.001")),
            (Decimal("100"), "BUY", Decimal("1")),
            (Decimal("100"), "BUY", Decimal("NaN")),
            (Decimal("100"), "BUY", Decimal("Infinity")),
            (Decimal("100"), "BUY", 0.001),
        ]

        for price, action, rate in cases:
            with self.subTest(
                price=price,
                action=action,
                rate=rate,
            ):
                with self.assertRaises(ValueError):
                    apply_slippage(price, action, rate)

                    
if __name__ == "__main__":
    unittest.main()