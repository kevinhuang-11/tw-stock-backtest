import unittest
from decimal import Decimal

from tw_stock_backtest.analysis.forward_returns import (
    evaluate_forward_return,
)
from tw_stock_backtest.analysis.forward_returns import (
    evaluate_forward_return,
    evaluate_rankings,
)


class TestForwardReturn(unittest.TestCase):
    def setUp(self):
        self.dates = [
            "2024-01-02",  # 訊號日
            "2024-01-03",  # 第 1 日
            "2024-01-04",  # 第 2 日
            "2024-01-05",  # 第 3 日
            "2024-01-08",  # 第 4 日
            "2024-01-09",  # 第 5 日
        ]

        self.records = [
            {
                "stock_id": "AAA",
                "date": day,
                "open": Decimal("100"),
                "close": Decimal("101"),
            }
            for day in self.dates
        ]

        # 故意設定不同價格，確認不使用訊號日收盤起算。
        self.records[0]["close"] = Decimal("50")
        self.records[-1]["close"] = Decimal("120")

    def evaluate(self, horizon=5):
        return evaluate_forward_return(
            self.records,
            "2024-01-02",
            self.dates,
            horizon=horizon,
        )

    def test_five_days_uses_next_open(self):
        result = self.evaluate()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entry_date"], "2024-01-03")
        self.assertEqual(result["exit_date"], "2024-01-09")
        self.assertEqual(result["entry_price"], Decimal("100"))
        self.assertEqual(result["exit_price"], Decimal("120"))
        self.assertEqual(result["price_return"], Decimal("0.2"))

    def test_one_day_uses_entry_day_close(self):
        result = self.evaluate(horizon=1)

        self.assertEqual(result["entry_date"], "2024-01-03")
        self.assertEqual(result["exit_date"], "2024-01-03")
        self.assertEqual(result["price_return"], Decimal("0.01"))

    def test_negative_return(self):
        self.records[-1]["close"] = Decimal("90")

        result = self.evaluate()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["price_return"], Decimal("-0.1"))

    def test_insufficient_future_is_not_zero_return(self):
        result = self.evaluate(horizon=20)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(
            result["reason"],
            "insufficient_future_dates",
        )
        self.assertIsNone(result["price_return"])

    def test_missing_exit_does_not_shift_date(self):
        # 缺少第 2 日行情，但共用日期清單仍保留這一天。
        self.records = [
            row
            for row in self.records
            if row["date"] != "2024-01-04"
        ]

        result = self.evaluate(horizon=2)

        self.assertEqual(result["exit_date"], "2024-01-04")
        self.assertEqual(result["reason"], "missing_exit_record")
        self.assertIsNone(result["price_return"])

    def test_halted_entry_does_not_delay_entry(self):
        self.records[1]["open"] = None
        self.records[1]["tradable"] = False

        result = self.evaluate()

        self.assertEqual(result["entry_date"], "2024-01-03")
        self.assertEqual(
            result["reason"],
            "unavailable_entry_price",
        )
        self.assertIsNone(result["price_return"])

    def test_halted_exit_does_not_use_stale_price(self):
        self.records[-1]["close"] = None
        self.records[-1]["tradable"] = False
        self.records[-1]["valuation_close"] = Decimal("110")

        result = self.evaluate()

        self.assertEqual(
            result["reason"],
            "unavailable_exit_price",
        )
        self.assertIsNone(result["price_return"])

    def test_invalid_horizons(self):
        for horizon in (0, -1, True, 1.5):
            with self.subTest(horizon=horizon):
                with self.assertRaises(ValueError):
                    self.evaluate(horizon=horizon)

    def test_invalid_calendars(self):
        calendars = [
            self.dates[::-1],
            self.dates + [self.dates[-1]],
            self.dates[1:],
        ]

        for calendar in calendars:
            with self.subTest(calendar=calendar):
                with self.assertRaises(ValueError):
                    evaluate_forward_return(
                        self.records,
                        "2024-01-02",
                        calendar,
                        horizon=5,
                    )

class TestRankingEvaluation(unittest.TestCase):
    def setUp(self):
        self.dates = [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
        ]

        # 第 1 日訊號，下一日開盤起算。
        # AAA：100 → 120，+20%。
        # BBB：100 → 90，-10%。
        # 股票池平均為 +5%。
        self.records = {
            stock_id: [
                {
                    "stock_id": stock_id,
                    "date": day,
                    "open": Decimal("100"),
                    "close": Decimal(close),
                }
                for day in self.dates
            ]
            for stock_id, close in (
                ("AAA", "120"),
                ("BBB", "90"),
            )
        }

        self.history = [{
            "date": "2024-01-02",
            "candidates": [
                {"stock_id": "AAA"},
                {"stock_id": "BBB"},
            ],
            "errors": {},
        }]

    def evaluate(self, top_ns=(1, 2)):
        return evaluate_rankings(
            self.history,
            self.records,
            self.dates,
            horizons=(1,),
            top_ns=top_ns,
        )

    def test_top_one_and_top_two(self):
        result = self.evaluate()
        first, second = result["summaries"]

        self.assertEqual(
            first["average_selected_return"], Decimal("0.2")
        )
        self.assertEqual(
            first["average_universe_return"], Decimal("0.05")
        )
        self.assertEqual(
            first["average_excess_return"], Decimal("0.15")
        )
        self.assertEqual(first["outperform_ratio"], Decimal("1"))

        # 前兩名等於整個兩檔股票池，報酬差應為零。
        self.assertEqual(
            second["average_selected_return"], Decimal("0.05")
        )
        self.assertEqual(
            second["average_excess_return"], Decimal("0")
        )
        self.assertEqual(second["outperform_ratio"], Decimal("0"))

    def test_insufficient_candidates_are_not_filled(self):
        self.history[0]["candidates"] = [{"stock_id": "AAA"}]

        result = self.evaluate(top_ns=(2,))

        detail = result["details"][0]
        self.assertEqual(detail["selected_count"], 1)
        self.assertEqual(detail["selected_stocks"], ["AAA"])
        self.assertEqual(detail["selected_return"], Decimal("0.2"))

    def test_missing_universe_price_excludes_comparison(self):
        # BBB 沒被前一名選中，但仍屬於比較用的股票池。
        self.records["BBB"][1]["close"] = None

        result = self.evaluate(top_ns=(1,))

        summary = result["summaries"][0]
        self.assertEqual(summary["evaluated_days"], 0)
        self.assertEqual(summary["unavailable_days"], 1)
        self.assertIsNone(summary["average_selected_return"])
        self.assertEqual(
            result["details"][0]["unavailable_stocks"],
            {"BBB": "unavailable_exit_price"},
        )

    def test_no_candidates_is_not_zero_return(self):
        self.history[0]["candidates"] = []

        result = self.evaluate(top_ns=(1,))

        summary = result["summaries"][0]
        self.assertIsNone(summary["average_selected_return"])
        self.assertEqual(
            summary["unavailable_reasons"],
            {"no_candidates": 1},
        )

    def test_incomplete_ranking_is_reported(self):
        self.history[0]["errors"] = {"BBB": "暖機資料不足"}

        result = self.evaluate(top_ns=(1,))

        self.assertEqual(
            result["details"][0]["reason"],
            "ranking_incomplete",
        )

    def test_tail_dates_are_excluded_from_average(self):
        self.history.append({
            "date": "2024-01-04",
            "candidates": [{"stock_id": "AAA"}],
            "errors": {},
        })

        result = self.evaluate(top_ns=(1,))

        summary = result["summaries"][0]
        self.assertEqual(summary["signal_days"], 2)
        self.assertEqual(summary["evaluated_days"], 1)
        self.assertEqual(summary["unavailable_days"], 1)
        self.assertEqual(
            summary["average_selected_return"], Decimal("0.2")
        )

if __name__ == "__main__":
    unittest.main()