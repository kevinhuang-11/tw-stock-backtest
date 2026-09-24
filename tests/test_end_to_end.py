import csv
import json
import os
import subprocess
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.data.database import save_records


TEST_CONFIG = """
[universe]
stocks = ["AAA"]

[screening]
short_window = 2
long_window = 3
volume_window = 1
min_volume_ratio = "1"
momentum_window = 1
top_n = 1

[strategy]
short_window = 2
long_window = 3

[backtest]
initial_cash = "1000"
quantity = 10
max_positions = 1

[costs]
commission_rate = "0.01"
commission_discount = "1"
minimum_commission = "0"
sell_tax_rate = "0.01"
commission_rounding = "ROUND_DOWN"
tax_rounding = "ROUND_DOWN"

[download]
timeout_seconds = 30
request_interval_seconds = 3
max_attempts = 3
retry_wait_seconds = 3

[storage]
database_path = "data/stocks.db"
"""


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "config.toml"
        self.db_path = self.root / "data" / "stocks.db"
        self.reports_path = self.root / "reports"

        self.config_path.write_text(
            TEST_CONFIG,
            encoding="utf-8",
        )

        # Matplotlib 的快取也放到臨時目錄。
        self.environment = os.environ.copy()
        self.environment["MPLCONFIGDIR"] = str(
            self.root / "matplotlib-cache"
        )
        self.environment["MPLBACKEND"] = "Agg"
        self.environment["PYTHONIOENCODING"] = "utf-8"

        start = date(2026, 1, 1)
        records = []

        for index in range(4):
            price = Decimal(10 + index)
            volume = 100 * (2 ** index)

            records.append(
                {
                    "stock_id": "AAA",
                    "date": (
                        start + timedelta(days=index)
                    ).isoformat(),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": volume,
                    "turnover": int(price * volume),
                    "trade_count": 10,
                }
            )

        save_records(
            records,
            db_path=self.db_path,
        )

    def run_cli(self, module, *arguments):
        """使用目前虛擬環境的 Python 啟動真正的 CLI。"""
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                module,
                *arguments,
            ],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=(
                f"命令列執行失敗：{module}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            ),
        )

        return completed

    def test_backtest_export_and_plot(self):
        # 1. 實際執行回測與報表匯出。
        self.run_cli(
            "tw_stock_backtest.cli.run_portfolio",
            "--config",
            str(self.config_path),
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-04",
            "--export",
            "--output-dir",
            str(self.reports_path),
        )

        self.assertTrue(self.reports_path.is_dir())

        report_dirs = [
            path
            for path in self.reports_path.iterdir()
            if path.is_dir()
        ]
        self.assertEqual(len(report_dirs), 1)
        report_dir = report_dirs[0]

        # 2. 確認五個報表檔案都存在。
        expected_files = {
            "settings.json",
            "results.json",
            "input_data.json",
            "trades.csv",
            "equity.csv",
        }

        self.assertEqual(
            {path.name for path in report_dir.iterdir()},
            expected_files,
        )

        # 3. 檢查輸出的設定確實來自臨時 config。
        settings_document = json.loads(
            (report_dir / "settings.json").read_text(
                encoding="utf-8"
            )
        )

        exported_settings = settings_document["settings"]

        self.assertEqual(
            exported_settings["universe"]["stocks"],
            ["AAA"],
        )
        self.assertEqual(
            Path(exported_settings["storage"]["database_path"]),
            self.db_path.resolve(),
        )

        # 4. 檢查策略結果與下一日成交行為。
        results_document = json.loads(
            (report_dir / "results.json").read_text(
                encoding="utf-8"
            )
        )

        strategy = results_document["strategy"]["result"]
        benchmark = results_document["benchmark"]["result"]

        self.assertEqual(
            Decimal(strategy["final_equity"]),
            Decimal("999"),
        )
        self.assertEqual(
            Decimal(strategy["cash"]),
            Decimal("869"),
        )
        self.assertEqual(strategy["holdings"], {"AAA": 10})
        self.assertEqual(len(strategy["trades"]), 1)

        trade = strategy["trades"][0]

        self.assertEqual(trade["signal_date"], "2026-01-03")
        self.assertEqual(trade["date"], "2026-01-04")
        self.assertEqual(trade["action"], "BUY")

        # 基準：第一天買 99 股，價款 990、手續費 9，
        # 剩餘現金 1；期末 99 × 13 + 1 = 1288。
        self.assertEqual(
            benchmark["holdings"],
            {"AAA": 99},
        )
        self.assertEqual(
            Decimal(benchmark["final_equity"]),
            Decimal("1288"),
        )

        # 5. 檢查行情快照與資產 CSV。
        input_document = json.loads(
            (report_dir / "input_data.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            len(input_document["records_by_stock"]["AAA"]),
            4,
        )

        with (report_dir / "equity.csv").open(
            encoding="utf-8-sig",
            newline="",
        ) as file:
            equity_rows = list(csv.DictReader(file))

        self.assertEqual(len(equity_rows), 4)
        self.assertEqual(
            Decimal(equity_rows[-1]["strategy_equity"]),
            Decimal("999"),
        )

        # 6. 實際執行繪圖入口。
        self.run_cli(
            "tw_stock_backtest.cli.plot_report",
            "--report-dir",
            str(report_dir),
        )

        image_path = report_dir / "performance.png"
        self.assertTrue(image_path.is_file())

        # 不只檢查檔名：實際解碼圖片，確認輸出可讀。
        from matplotlib.image import imread

        image = imread(image_path)

        self.assertGreater(image.shape[0], 100)
        self.assertGreater(image.shape[1], 100)
        self.assertGreater(float(image.max() - image.min()), 0)


if __name__ == "__main__":
    unittest.main()