import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.analysis.screening import evaluate_stock


class TestScreening(unittest.TestCase):
    def setUp(self):
        self.as_of = "2026-01-21"

        # 測試自行指定設定，不讀取日常使用的 config.toml。
        self.parameters = {
            "short_window": 5,
            "long_window": 20,
            "volume_window": 20,
            "min_volume_ratio": Decimal("1"),
            "momentum_window": 20,
        }

    def make_records(self, descending=False, last_volume=200):
        start = date(2026, 1, 1)
        records = []

        for index in range(21):
            close = 120 - index if descending else 100 + index

            records.append(
                {
                    "stock_id": "2330",
                    "date": (
                        start + timedelta(days=index)
                    ).isoformat(),
                    "close": Decimal(close),
                    "volume": (
                        last_volume if index == 20 else 100
                    ),
                }
            )

        return records

    def evaluate(self, records, as_of=None, **overrides):
        parameters = {
            **self.parameters,
            **overrides,
        }

        return evaluate_stock(
            records,
            self.as_of if as_of is None else as_of,
            **parameters,
        )

    def test_rising_stock_is_selected(self):
        result = self.evaluate(self.make_records())

        self.assertTrue(result["selected"])
        self.assertEqual(result["close"], Decimal("120"))
        self.assertEqual(result["short_ma"], Decimal("118"))
        self.assertEqual(result["long_ma"], Decimal("110.5"))
        self.assertEqual(
            result["previous_avg_volume"],
            Decimal("100"),
        )
        self.assertEqual(result["volume_ratio"], Decimal("2"))
        self.assertEqual(result["momentum"], Decimal("0.2"))
        self.assertEqual(result["failed_conditions"], [])

    def test_falling_stock_is_not_selected(self):
        result = self.evaluate(
            self.make_records(descending=True)
        )

        self.assertFalse(result["selected"])
        self.assertIn(
            "未符合 Close > SMA5 > SMA20",
            result["failed_conditions"],
        )

    def test_equal_volume_does_not_pass(self):
        result = self.evaluate(
            self.make_records(last_volume=100)
        )

        self.assertFalse(result["selected"])
        self.assertEqual(result["volume_ratio"], Decimal("1"))

    def test_insufficient_records(self):
        with self.assertRaises(ValueError):
            self.evaluate(self.make_records()[1:])

    def test_missing_analysis_date(self):
        with self.assertRaises(ValueError):
            self.evaluate(
                self.make_records(),
                as_of="2026-01-22",
            )

    def test_zero_previous_average_volume(self):
        records = self.make_records()

        for record in records[:-1]:
            record["volume"] = 0

        with self.assertRaises(ValueError):
            self.evaluate(records)

    def test_future_data_does_not_change_result(self):
        records = self.make_records()
        original = self.evaluate(records)

        records.append(
            {
                "stock_id": "2330",
                "date": "2026-01-22",
                "close": Decimal("9999"),
                "volume": 999999,
            }
        )

        self.assertEqual(self.evaluate(records), original)

    def test_custom_windows(self):
        # 只提供最後 6 筆，價格為 115、116、117、118、119、120。
        records = self.make_records()[-6:]

        # 成交量為 10、20、30、40、60、100。
        for record, volume in zip(
            records,
            [10, 20, 30, 40, 60, 100],
        ):
            record["volume"] = volume

        result = self.evaluate(
            records,
            short_window=2,
            long_window=4,
            volume_window=2,
            momentum_window=5,
        )

        self.assertEqual(
            result["short_ma"],
            Decimal("119.5"),
        )
        self.assertEqual(
            result["long_ma"],
            Decimal("118.5"),
        )

        # 前 2 筆成交量是 40、60，不包含當日的 100。
        self.assertEqual(
            result["previous_avg_volume"],
            Decimal("50"),
        )
        self.assertEqual(result["volume_ratio"], Decimal("2"))

        self.assertEqual(
            result["momentum"],
            Decimal("120") / Decimal("115") - Decimal("1"),
        )
        self.assertTrue(result["selected"])

    def test_custom_volume_threshold(self):
        # 量比為 2，門檻也設成 2，因為要求嚴格大於，所以不通過。
        result = self.evaluate(
            self.make_records(),
            min_volume_ratio=Decimal("2"),
        )

        self.assertFalse(result["selected"])
        self.assertIn(
            "量比未大於 2",
            result["failed_conditions"],
        )

    def test_each_window_can_determine_required_records(self):
        cases = (
            {"long_window": 22},
            {"volume_window": 21},
            {"momentum_window": 21},
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.evaluate(
                        self.make_records(),
                        **overrides,
                    )

    def test_invalid_parameters(self):
        cases = (
            {"short_window": 0},
            {"short_window": 20},
            {"volume_window": True},
            {"momentum_window": -1},
            {"min_volume_ratio": Decimal("-1")},
            {"min_volume_ratio": Decimal("NaN")},
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.evaluate(
                        self.make_records(),
                        **overrides,
                    )


if __name__ == "__main__":
    unittest.main()