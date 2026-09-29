import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tw_stock_backtest.cli.download_universe import download_batch


class TestDownloadBatch(unittest.TestCase):
    def setUp(self):
        self.stocks = [
            {
                "stock_id": "1101",
                "name": "測試公司一",
                "listed_date": "1962-02-09",
            },
            {
                "stock_id": "1102",
                "name": "測試公司二",
                "listed_date": "1966-11-15",
            },
        ]

    def run_batch(self, stocks=None):
        return download_batch(
            self.stocks if stocks is None else stocks,
            "2026-08-01",
            "2026-08-31",
            config_path=Path("config.toml"),
            interval_seconds=0,
        )

    @patch("tw_stock_backtest.cli.download_universe.time.sleep")
    @patch("tw_stock_backtest.cli.download_universe.subprocess.run")
    def test_success_and_command_arguments(self, mock_run, mock_sleep):
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        results = self.run_batch()

        self.assertEqual(
            [item["status"] for item in results],
            ["completed", "completed"],
        )
        self.assertEqual(mock_run.call_count, 2)

        command = mock_run.call_args_list[0].args[0]

        self.assertEqual(
            command[command.index("--stock") + 1],
            "1101",
        )
        self.assertEqual(
            command[command.index("--start") + 1],
            "2026-08-01",
        )
        self.assertEqual(
            command[command.index("--end") + 1],
            "2026-08-31",
        )

        mock_sleep.assert_called_once_with(0)

    @patch("tw_stock_backtest.cli.download_universe.time.sleep")
    @patch("tw_stock_backtest.cli.download_universe.subprocess.run")
    def test_failed_stock_does_not_block_next(
        self,
        mock_run,
        mock_sleep,
    ):
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 0),
        ]

        results = self.run_batch()

        self.assertEqual(
            [item["status"] for item in results],
            ["failed", "completed"],
        )
        self.assertEqual(mock_run.call_count, 2)

    @patch("tw_stock_backtest.cli.download_universe.time.sleep")
    @patch("tw_stock_backtest.cli.download_universe.subprocess.run")
    def test_process_error_does_not_block_next(
        self,
        mock_run,
        mock_sleep,
    ):
        mock_run.side_effect = [
            OSError("模擬無法啟動"),
            subprocess.CompletedProcess([], 0),
        ]

        results = self.run_batch()

        self.assertEqual(
            [item["status"] for item in results],
            ["failed", "completed"],
        )

    @patch("tw_stock_backtest.cli.download_universe.time.sleep")
    @patch("tw_stock_backtest.cli.download_universe.subprocess.run")
    def test_listing_date_controls_download_range(
        self,
        mock_run,
        mock_sleep,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        stocks = [
            {
                "stock_id": "1101",
                "name": "期間內上市",
                "listed_date": "2026-08-15",
            },
            {
                "stock_id": "1102",
                "name": "期間後上市",
                "listed_date": "2026-09-01",
            },
        ]

        results = self.run_batch(stocks)

        self.assertEqual(
            [item["status"] for item in results],
            ["completed", "skipped"],
        )
        mock_run.assert_called_once()

        command = mock_run.call_args.args[0]

        self.assertEqual(
            command[command.index("--start") + 1],
            "2026-08-15",
        )


if __name__ == "__main__":
    unittest.main()