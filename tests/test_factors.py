import unittest
from copy import deepcopy
from decimal import Decimal
from tw_stock_backtest.analysis.factors import (
    calculate_factor_row,
    rank_factor_candidates,
)



class TestFactorRanking(unittest.TestCase):
    def setUp(self):
        self.weights = {
            "momentum": Decimal("1"),
            "trend": Decimal("1"),
            "volatility": Decimal("1"),
        }

        self.rows = [
            self.make_row("AAA", "0.30", "0.01", "0.02"),
            self.make_row("BBB", "0.20", "0.02", "0.01"),
            self.make_row("CCC", "0.10", "0.03", "0.03"),
        ]

    def make_row(self, stock_id, momentum, trend, volatility):
        return {
            "stock_id": stock_id,
            "date": "2026-01-05",
            "momentum": Decimal(momentum),
            "trend": Decimal(trend),
            "volatility": Decimal(volatility),
        }

    def rank(self, rows=None, weights=None):
        return rank_factor_candidates(
            self.rows if rows is None else rows,
            weights=self.weights if weights is None else weights,
        )

    def test_equal_weight_ranking(self):
        original = deepcopy(self.rows)
        result = self.rank()

        self.assertEqual(
            [row["stock_id"] for row in result],
            ["BBB", "AAA", "CCC"],
        )

        # BBB：動能 50、趨勢 50、低波動 100。
        self.assertEqual(
            result[0]["factor_scores"],
            {
                "momentum": Decimal("50"),
                "trend": Decimal("50"),
                "volatility": Decimal("100"),
            },
        )
        self.assertEqual(
            result[0]["score"],
            Decimal("200") / Decimal("3"),
        )

        self.assertEqual(result[1]["score"], Decimal("50"))
        self.assertEqual(
            [row["rank"] for row in result],
            [1, 2, 3],
        )

        # 排名不應修改輸入資料。
        self.assertEqual(self.rows, original)

    def test_ties_use_average_rank(self):
        rows = [
            self.make_row("BBB", "0.20", "0.01", "0.02"),
            self.make_row("AAA", "0.20", "0.01", "0.02"),
            self.make_row("CCC", "0.10", "0.01", "0.02"),
        ]

        result = self.rank(rows=rows)

        self.assertEqual(
            [row["stock_id"] for row in result],
            ["AAA", "BBB", "CCC"],
        )

        # 動能最高兩檔平手，平均分為 (50 + 100) / 2。
        self.assertEqual(
            result[0]["factor_scores"]["momentum"],
            Decimal("75"),
        )

        # 趨勢和波動全部相同，都是中間分數 50。
        self.assertEqual(
            result[0]["factor_scores"]["trend"],
            Decimal("50"),
        )
        self.assertEqual(
            result[0]["factor_scores"]["volatility"],
            Decimal("50"),
        )

    def test_single_stock_gets_neutral_score(self):
        result = self.rank(rows=self.rows[:1])

        self.assertEqual(result[0]["score"], Decimal("50"))

    def test_custom_weights(self):
        result = self.rank(
            weights={
                "momentum": Decimal("1"),
                "trend": Decimal("0"),
                "volatility": Decimal("0"),
            }
        )

        self.assertEqual(
            [row["stock_id"] for row in result],
            ["AAA", "BBB", "CCC"],
        )
        self.assertEqual(result[0]["score"], Decimal("100"))

    def test_invalid_weights(self):
        cases = [
            {
                "momentum": Decimal("0"),
                "trend": Decimal("0"),
                "volatility": Decimal("0"),
            },
            {
                "momentum": Decimal("-1"),
                "trend": Decimal("1"),
                "volatility": Decimal("1"),
            },
            {
                "momentum": Decimal("NaN"),
                "trend": Decimal("1"),
                "volatility": Decimal("1"),
            },
            {
                "momentum": Decimal("1"),
            },
        ]

        for weights in cases:
            with self.subTest(weights=weights):
                with self.assertRaises(ValueError):
                    self.rank(weights=weights)

    def test_invalid_rows(self):
        cases = []

        duplicate = deepcopy(self.rows)
        duplicate[1]["stock_id"] = "AAA"
        cases.append(duplicate)

        different_date = deepcopy(self.rows)
        different_date[1]["date"] = "2026-01-06"
        cases.append(different_date)

        negative_volatility = deepcopy(self.rows)
        negative_volatility[0]["volatility"] = Decimal("-0.01")
        cases.append(negative_volatility)

        invalid_value = deepcopy(self.rows)
        invalid_value[0]["momentum"] = Decimal("Infinity")
        cases.append(invalid_value)

        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.rank(rows=rows)

    def test_empty_input(self):
        self.assertEqual(self.rank(rows=[]), [])

class TestCalculateFactorRow(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "short_window": 2,
            "long_window": 3,
            "momentum_window": 2,
            "volatility_window": 2,
        }

        self.records = [
            {
                "stock_id": "AAA",
                "date": "2026-01-01",
                "close": Decimal("10"),
            },
            {
                "stock_id": "AAA",
                "date": "2026-01-02",
                "close": Decimal("20"),
            },
            {
                "stock_id": "AAA",
                "date": "2026-01-03",
                "close": Decimal("20"),
            },
        ]

    def calculate(self, records=None, as_of="2026-01-03"):
        return calculate_factor_row(
            self.records if records is None else records,
            as_of,
            factor_settings=self.settings,
        )

    def test_known_values(self):
        result = self.calculate()

        # 動能：20 / 10 - 1 = 1。
        self.assertEqual(result["momentum"], Decimal("1"))

        # 短均線 20，長均線 50/3，趨勢約 0.2。
        self.assertAlmostEqual(
            result["trend"],
            Decimal("0.2"),
            places=24,
        )

        # 每日報酬率為 1、0；母體標準差為 0.5。
        self.assertEqual(
            result["volatility"],
            Decimal("0.5"),
        )

    def test_future_data_does_not_change_result(self):
        original = self.calculate()

        self.records.append(
            {
                "stock_id": "AAA",
                "date": "2026-01-04",
                "close": Decimal("9999"),
            }
        )

        self.assertEqual(self.calculate(), original)

    def test_insufficient_history(self):
        with self.assertRaises(ValueError):
            self.calculate(records=self.records[1:])

    def test_missing_analysis_date(self):
        with self.assertRaises(ValueError):
            self.calculate(as_of="2026-01-04")

if __name__ == "__main__":
    unittest.main()