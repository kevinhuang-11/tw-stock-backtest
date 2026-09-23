import unittest
from decimal import Decimal, ROUND_HALF_UP

from costs import CostSettings, calculate_transaction


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


if __name__ == "__main__":
    unittest.main()