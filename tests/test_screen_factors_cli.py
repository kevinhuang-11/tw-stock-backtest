import sqlite3
import unittest
from unittest.mock import patch

from tw_stock_backtest.cli.screen_factors import collect_factor_rows


MODULE = "tw_stock_backtest.cli.screen_factors"


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
    def test_database_error_is_not_hidden(self, mock_load):
        mock_load.side_effect = sqlite3.OperationalError(
            "資料庫無法讀取"
        )

        with self.assertRaises(sqlite3.OperationalError):
            self.collect()


if __name__ == "__main__":
    unittest.main()