import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from tw_stock_backtest.cli.download_universe import download_batch
from tempfile import TemporaryDirectory

from tw_stock_backtest.cli.download_universe import (
    load_progress,
    save_progress,
    select_pending_stocks,
)


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
    def test_resume_retries_failed_and_unstarted_stocks(self):
        stocks = self.stocks + [
            {
                "stock_id": "1103",
                "name": "測試公司三",
                "listed_date": "1969-11-14",
            },
        ]

        results = [
            {"stock_id": "1101", "status": "completed"},
            {"stock_id": "1102", "status": "failed"},
        ]

        pending = select_pending_stocks(stocks, results)

        self.assertEqual(
            [stock["stock_id"] for stock in pending],
            ["1102", "1103"],
        )

    def test_progress_save_and_load(self):
        job = {
            "start": "2026-08-01",
            "end": "2026-08-31",
            "stocks": self.stocks,
        }
        report = {
            "schema_version": 1,
            "job": job,
            "results": [
                {"stock_id": "1101", "status": "completed"},
            ],
        }

        with TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"

            save_progress(report, path)
            loaded = load_progress(path, job)

            self.assertEqual(loaded, report)

    def test_different_job_cannot_resume(self):
        job = {
            "start": "2026-08-01",
            "end": "2026-08-31",
            "stocks": self.stocks,
        }
        report = {
            "schema_version": 1,
            "job": job,
            "results": [],
        }

        with TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            save_progress(report, path)

            changed_job = {
                **job,
                "end": "2026-09-22",
            }

            with self.assertRaises(ValueError):
                load_progress(path, changed_job)

    @patch("tw_stock_backtest.cli.download_universe.time.sleep")
    @patch("tw_stock_backtest.cli.download_universe.subprocess.run")
    def test_progress_is_recorded_before_next_stock(
        self,
        mock_run,
        mock_sleep,
    ):
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0),
            KeyboardInterrupt(),
        ]
        recorded = []

        with self.assertRaises(KeyboardInterrupt):
            download_batch(
                self.stocks,
                "2026-08-01",
                "2026-08-31",
                config_path=Path("config.toml"),
                interval_seconds=0,
                on_result=recorded.append,
            )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["stock_id"], "1101")
        self.assertEqual(recorded[0]["status"], "completed")
        

if __name__ == "__main__":
    unittest.main()