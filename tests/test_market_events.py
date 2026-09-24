import unittest
from copy import deepcopy
from decimal import Decimal

from tw_stock_backtest.data.market_events import (
    prepare_market_records,
)


class TestMarketEvents(unittest.TestCase):
    def setUp(self):
        self.events = {
            ("AAA", "2026-01-06"): {
                "reason": "人工測試停牌事件",
                "source": "test fixture",
            }
        }

        self.records = [
            self.make_normal("2026-01-05", "100"),
            {
                "stock_id": "AAA",
                "date": "2026-01-06",
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": 0,
                "turnover": 0,
                "trade_count": 0,
            },
            self.make_normal("2026-01-07", "120"),
        ]

    def make_normal(self, record_date, price):
        value = Decimal(price)

        return {
            "stock_id": "AAA",
            "date": record_date,
            "open": value,
            "high": value,
            "low": value,
            "close": value,
            "volume": 100,
            "turnover": int(value * 100),
            "trade_count": 10,
        }

    def prepare(self, records=None, events=None):
        return prepare_market_records(
            self.records if records is None else records,
            confirmed_halts=(
                self.events if events is None else events
            ),
        )

    def test_normal_record_uses_its_own_close(self):
        result = self.prepare()

        self.assertTrue(result[0]["tradable"])
        self.assertEqual(
            result[0]["valuation_close"],
            Decimal("100"),
        )
        self.assertEqual(
            result[0]["valuation_price_date"],
            "2026-01-05",
        )

    def test_halt_keeps_raw_prices_and_does_not_modify_input(self):
        original = deepcopy(self.records)

        result = self.prepare()
        halted = result[1]

        self.assertFalse(halted["tradable"])
        self.assertIsNone(halted["open"])
        self.assertIsNone(halted["close"])
        self.assertEqual(
            halted["valuation_close"],
            Decimal("100"),
        )
        self.assertEqual(
            halted["valuation_price_date"],
            "2026-01-05",
        )
        self.assertEqual(
            halted["market_status"],
            "CONFIRMED_HALT",
        )

        self.assertEqual(self.records, original)

    def test_future_price_is_not_used_for_halt_valuation(self):
        result = self.prepare()

        # 停牌日不能使用隔天才出現的 120 元。
        self.assertEqual(
            result[1]["valuation_close"],
            Decimal("100"),
        )
        self.assertEqual(
            result[2]["valuation_close"],
            Decimal("120"),
        )
        self.assertEqual(
            result[2]["valuation_price_date"],
            "2026-01-07",
        )

    def test_unconfirmed_missing_prices_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "未確認為停牌",
        ):
            self.prepare(events={})

    def test_halt_without_prior_price_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "沒有有效收盤價",
        ):
            self.prepare(records=self.records[1:])

    def test_conflicting_event_and_prices_are_rejected(self):
        self.records[1] = self.make_normal(
            "2026-01-06",
            "110",
        )

        with self.assertRaisesRegex(
            ValueError,
            "不一致",
        ):
            self.prepare()


if __name__ == "__main__":
    unittest.main()