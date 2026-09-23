import unittest
from datetime import date, timedelta
from decimal import Decimal

from screening import evaluate_stock


class TestScreening(unittest.TestCase):
    def make_records(self, rising=True, last_volume=200):
        records = []

        for index in range(21):
            price = 100 + index if rising else 120 - index

            records.append({
                "stock_id": "2330",
                "date": (
                    date(2025, 1, 1) + timedelta(days=index)
                ).isoformat(),
                "close": Decimal(price),
                "volume": last_volume if index == 20 else 100,
            })

        return records

    def test_rising_price_and_higher_volume_selected(self):
        result = evaluate_stock(
            self.make_records(),
            "2025-01-21",
        )

        self.assertTrue(result["selected"])
        self.assertEqual(result["close"], Decimal("120"))
        self.assertEqual(result["sma5"], Decimal("118"))
        self.assertEqual(result["sma20"], Decimal("110.5"))
        self.assertEqual(result["previous_avg_volume"], Decimal("100"))
        self.assertEqual(result["volume_ratio"], Decimal("2"))
        self.assertEqual(result["momentum20"], Decimal("0.2"))
        self.assertEqual(result["failed_conditions"], [])

    def test_falling_price_is_not_selected(self):
        result = evaluate_stock(
            self.make_records(rising=False),
            "2025-01-21",
        )

        self.assertFalse(result["selected"])
        self.assertIn(
            "未符合 Close > SMA5 > SMA20",
            result["failed_conditions"],
        )

    def test_equal_volume_is_not_selected(self):
        result = evaluate_stock(
            self.make_records(last_volume=100),
            "2025-01-21",
        )

        self.assertEqual(result["volume_ratio"], Decimal("1"))
        self.assertFalse(result["selected"])

    def test_insufficient_history_is_rejected(self):
        records = self.make_records()[1:]

        with self.assertRaises(ValueError):
            evaluate_stock(records, "2025-01-21")

    def test_missing_target_date_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_stock(
                self.make_records(),
                "2025-01-22",
            )

    def test_zero_average_volume_is_rejected(self):
        records = self.make_records()

        for record in records[:-1]:
            record["volume"] = 0

        with self.assertRaises(ValueError):
            evaluate_stock(records, "2025-01-21")

    def test_future_data_does_not_change_result(self):
        records = self.make_records()
        original = evaluate_stock(records, "2025-01-21")

        records.append({
            "stock_id": "2330",
            "date": "2025-01-22",
            "close": Decimal("9999"),
            "volume": 999999,
        })

        changed = evaluate_stock(records, "2025-01-21")

        self.assertEqual(changed, original)


if __name__ == "__main__":
    unittest.main()