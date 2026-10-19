import sqlite3
import unittest
from decimal import Decimal
from unittest.mock import patch

from tw_stock_backtest.factor_rows import collect_factor_rows

MODULE = "tw_stock_backtest.factor_rows"


class TestCollectFactorRows(unittest.TestCase):
    def setUp(self):
        self.stocks = [
            {"stock_id": "1101", "name": "台泥"},
            {"stock_id": "1102", "name": "亞泥"},
        ]

    def collect(self, stocks=None):
        return collect_factor_rows(
            self.stocks if stocks is None else stocks,
            "2026-08-31",
            db_path="unused.db",
            factor_settings={},
        )

    @patch(f"{MODULE}.calculate_factor_row")
    @patch(f"{MODULE}.load_records")
    def test_valid_stock_is_collected(self, mock_load, mock_calculate):
        mock_load.return_value = [{"date": "2026-08-31"}]
        mock_calculate.return_value = {"stock_id": "1101"}

        rows, errors = self.collect(self.stocks[:1])

        self.assertEqual(rows, [{"stock_id": "1101"}])
        self.assertEqual(errors, [])

        mock_load.assert_called_once_with(
            "1101",
            "1900-01-01",
            "2026-08-31",
            db_path="unused.db",
        )

    @patch(f"{MODULE}.calculate_factor_row")
    @patch(f"{MODULE}.load_records")
    def test_stale_stock_does_not_block_next(
        self,
        mock_load,
        mock_calculate,
    ):
        mock_load.side_effect = [
            [{"date": "2026-08-28"}],
            [{"date": "2026-08-31"}],
        ]
        mock_calculate.return_value = {"stock_id": "1102"}

        rows, errors = self.collect()

        self.assertEqual(rows, [{"stock_id": "1102"}])
        self.assertEqual(errors[0]["stock_id"], "1101")
        self.assertIn("2026-08-28", errors[0]["reason"])
        mock_calculate.assert_called_once()

    @patch(f"{MODULE}.calculate_factor_row")
    @patch(f"{MODULE}.load_records")
    def test_empty_records_are_reported(self, mock_load, mock_calculate):
        mock_load.return_value = []

        rows, errors = self.collect(self.stocks[:1])

        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("沒有", errors[0]["reason"])
        mock_calculate.assert_not_called()

    @patch(f"{MODULE}.calculate_factor_row")
    @patch(f"{MODULE}.load_records")
    def test_factor_error_does_not_block_next(
        self,
        mock_load,
        mock_calculate,
    ):
        mock_load.return_value = [{"date": "2026-08-31"}]
        mock_calculate.side_effect = [
            ValueError("資料不足"),
            {"stock_id": "1102"},
        ]

        rows, errors = self.collect()

        self.assertEqual(rows, [{"stock_id": "1102"}])
        self.assertEqual(errors[0]["reason"], "資料不足")

    @patch(f"{MODULE}.load_records")
    def test_real_factor_calculation_ignores_old_missing_close(self, mock_load):
        mock_load.return_value = [
            {"stock_id": "1101", "date": "2026-08-27", "close": None},
            {"stock_id": "1101", "date": "2026-08-28", "close": Decimal("10")},
            {"stock_id": "1101", "date": "2026-08-31", "close": Decimal("12")},
        ]
        rows, errors = collect_factor_rows(
            self.stocks[:1],
            "2026-08-31",
            db_path="unused.db",
            factor_settings={
                "short_window": 1,
                "long_window": 2,
                "momentum_window": 1,
                "volatility_window": 1,
            },
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["momentum"], Decimal("0.2"))

    @patch(f"{MODULE}.load_records")
    def test_database_error_is_not_hidden(self, mock_load):
        mock_load.side_effect = sqlite3.OperationalError("資料庫無法讀取")

        with self.assertRaises(sqlite3.OperationalError):
            self.collect()


class TestAnalysisDateMessages(unittest.TestCase):
    """非交易日與行情未更新要給不同的說明。"""

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from tw_stock_backtest.data.database import save_records

        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "stocks.db"
        # 2026-07-31 是星期五、2026-08-03 是星期一，中間是週末。
        save_records(
            [
                {
                    "stock_id": "2330",
                    "date": day,
                    "open": Decimal("10"),
                    "high": Decimal("11"),
                    "low": Decimal("9"),
                    "close": Decimal("10"),
                    "volume": 1000,
                    "turnover": 10000,
                    "trade_count": 10,
                }
                for day in ("2026-07-30", "2026-07-31", "2026-08-03")
            ],
            db_path=self.db,
        )

    def reason(self, as_of):
        rows, errors = collect_factor_rows(
            [{"stock_id": "2330"}], as_of, db_path=self.db, factor_settings={}
        )
        self.assertEqual(rows, [])
        return errors[0]["reason"]

    def test_a_day_without_any_market_rows_does_not_look_ahead(self):
        # 週末與行情庫最新日之後，都只能說「這天全市場沒有行情」，不查之後的資料分辨。
        for as_of, latest in (("2026-08-01", "2026-07-31"), ("2026-08-05", "2026-08-03")):
            with self.subTest(as_of):
                self.assertEqual(
                    self.reason(as_of),
                    f"行情庫在 {as_of} 沒有任何股票的行情（休市日，或行情尚未更新到這天）；"
                    f"這檔最後行情日期為 {latest}",
                )

    def test_a_missing_row_when_others_trade_is_unconfirmed(self):
        from tw_stock_backtest.data.database import save_records

        other = {
            "stock_id": "2317",
            "date": "2026-08-04",
            "open": Decimal("10"),
            "high": Decimal("10"),
            "low": Decimal("10"),
            "close": Decimal("10"),
            "volume": 1,
            "turnover": 1,
            "trade_count": 1,
        }
        save_records([other], db_path=self.db)
        reason = self.reason("2026-08-04")
        self.assertIn("2026-08-04 其他股票有行情、這檔沒有列（原因未確認，不認定為停牌", reason)
        self.assertIn("最後行情日期為 2026-08-03", reason)

    def test_market_rows_only_checks_that_day(self):
        from tw_stock_backtest.data.database import market_has_rows

        self.assertFalse(market_has_rows("2026-08-01", db_path="no-such.db"))
        self.assertTrue(market_has_rows("2026-08-03", db_path=self.db))
        self.assertFalse(market_has_rows("2026-08-01", db_path=self.db))


if __name__ == "__main__":
    unittest.main()
