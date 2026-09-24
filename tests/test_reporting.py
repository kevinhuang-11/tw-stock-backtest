import csv
import json
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.reporting import export_portfolio_report


class TestReporting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.output_root = Path(self.temp_dir.name) / "reports"

        self.result = {
            "initial_cash": Decimal("1000"),
            "final_equity": Decimal("1001.25"),
            "trades": [],
            "equity_curve": [
                {
                    "date": "2026-01-05",
                    "cash": Decimal("501.25"),
                    "market_value": Decimal("500"),
                    "equity": Decimal("1001.25"),
                    "holdings": {"AAA": 10},
                },
            ],
        }

        self.benchmark = deepcopy(self.result)

        self.benchmark["trades"] = [
            {
                "stock_id": "AAA",
                "date": "2026-01-05",
                "action": "BUY",
                "price": Decimal("50"),
                "quantity": 10,
                "amount": Decimal("500"),
                "commission": Decimal("0"),
                "tax": Decimal("0"),
                "cash_change": Decimal("-500"),
                "cash_after": Decimal("500"),
            }
        ]

    def export(self):
        return export_portfolio_report(
            self.output_root,
            settings={
                "backtest": {
                    "initial_cash": Decimal("1000"),
                },
                "storage": {
                    "database_path": Path("/example/stocks.db"),
                },
            },
            start_text="2026-01-05",
            end_text="2026-01-05",
            records_by_stock={},
            result=self.result,
            performance={"max_drawdown": Decimal("0")},
            benchmark=self.benchmark,
            benchmark_performance={"max_drawdown": Decimal("0")},
        )

    def test_exported_files_and_values(self):
        report_dir = self.export()

        self.assertEqual(
            {path.name for path in report_dir.iterdir()},
            {
                "settings.json",
                "results.json",
                "input_data.json",
                "trades.csv",
                "equity.csv",
            },
        )

        results = json.loads(
            (report_dir / "results.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            results["strategy"]["result"]["final_equity"],
            "1001.25",
        )

        with (report_dir / "equity.csv").open(
            encoding="utf-8-sig",
            newline="",
        ) as file:
            rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_equity"], "1001.25")

        with (report_dir / "trades.csv").open(
            encoding="utf-8-sig",
            newline="",
        ) as file:
            trades = list(csv.DictReader(file))

        self.assertEqual(trades[0]["source"], "benchmark")
        self.assertEqual(trades[0]["signal_date"], "")
        self.assertEqual(trades[0]["price"], "50")

    def test_repeated_export_does_not_overwrite(self):
        first_dir = self.export()
        original_text = (
            first_dir / "results.json"
        ).read_text(encoding="utf-8")

        self.result["final_equity"] = Decimal("2000")
        second_dir = self.export()

        self.assertNotEqual(first_dir, second_dir)

        self.assertEqual(
            (first_dir / "results.json").read_text(
                encoding="utf-8"
            ),
            original_text,
        )

    def test_mismatched_dates_are_rejected(self):
        self.benchmark["equity_curve"][0]["date"] = "2026-01-06"

        with self.assertRaises(ValueError):
            self.export()

        self.assertFalse(self.output_root.exists())


if __name__ == "__main__":
    unittest.main()