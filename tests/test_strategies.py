import unittest
from decimal import Decimal

from strategies import moving_average_crossover


class TestMovingAverageCrossover(unittest.TestCase):
    def test_buy_cross(self):
        short_ma = [Decimal("9"), Decimal("11")]
        long_ma = [Decimal("10"), Decimal("10")]

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "BUY"],
        )

    def test_sell_cross(self):
        short_ma = [Decimal("11"), Decimal("9")]
        long_ma = [Decimal("10"), Decimal("10")]

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "SELL"],
        )

    def test_staying_above_does_not_repeat_buy(self):
        short_ma = [
            Decimal("9"),
            Decimal("11"),
            Decimal("12"),
        ]
        long_ma = [Decimal("10")] * 3

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "BUY", "NONE"],
        )

    def test_first_valid_pair_has_no_signal(self):
        short_ma = [Decimal("9"), Decimal("11")]
        long_ma = [None, Decimal("10")]

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "NONE"],
        )

    def test_missing_current_value(self):
        short_ma = [Decimal("9"), None]
        long_ma = [Decimal("10"), Decimal("10")]

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "NONE"],
        )

    def test_equal_then_above(self):
        short_ma = [Decimal("10"), Decimal("11")]
        long_ma = [Decimal("10"), Decimal("10")]

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "BUY"],
        )

    def test_equal_then_below(self):
        short_ma = [Decimal("10"), Decimal("9")]
        long_ma = [Decimal("10"), Decimal("10")]

        self.assertEqual(
            moving_average_crossover(short_ma, long_ma),
            ["NONE", "SELL"],
        )

    def test_equal_today_has_no_signal(self):
        for previous in (Decimal("9"), Decimal("11")):
            with self.subTest(previous=previous):
                self.assertEqual(
                    moving_average_crossover(
                        [previous, Decimal("10")],
                        [Decimal("10"), Decimal("10")],
                    ),
                    ["NONE", "NONE"],
                )

    def test_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            moving_average_crossover(
                [Decimal("10")],
                [],
            )

    def test_empty_input(self):
        self.assertEqual(
            moving_average_crossover([], []),
            [],
        )

    def test_future_change_does_not_change_past_signals(self):
        short_ma = [
            Decimal("9"),
            Decimal("11"),
            Decimal("12"),
        ]
        long_ma = [Decimal("10")] * 3

        original = moving_average_crossover(short_ma, long_ma)

        changed_short = short_ma.copy()
        changed_short[-1] = Decimal("8")

        changed = moving_average_crossover(changed_short, long_ma)

        self.assertEqual(original[:-1], changed[:-1])
        self.assertEqual(original[-1], "NONE")
        self.assertEqual(changed[-1], "SELL")


if __name__ == "__main__":
    unittest.main()