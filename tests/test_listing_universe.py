"""回測開始日還沒上市的股票不納入股票池，原因列在輸出與報表。"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from test_strategy_library import records
from test_ui_pages import PageCase, write

from tw_stock_backtest.cli import run_portfolio
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data.universe import (
    listing_exclusions,
    save_synthetic_universe,
    save_universe,
)
from tw_stock_backtest.strategies import from_settings
from tw_stock_backtest.strategy_comparison import compare_periods, compare_strategies


def listed(stock_id, listed_date):
    return {
        "stock_id": stock_id,
        "name": f"測試{stock_id}",
        "market": "上市",
        "industry": "其他業",
        "listed_date": listed_date,
        "cfi_code": "ESVUFR",
    }


class ListingUniverseTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', 'stocks = ["2330", "3054"]').replace(
                "quantity = 10", "quantity = 1"
            ),
            encoding="utf-8",
        )
        self.settings = load_config(self.config)
        self.db = Path(self.settings["storage"]["database_path"])
        # 3054 從 2026-08-20 才有行情（當天上市）。
        late = [r for r in records("3054") if r["date"] >= "2026-08-20"]
        save_records(records() + late, db_path=self.db)

    def write_listing(self):
        save_universe(
            [listed("2330", "1994-09-05"), listed("3054", "2026-08-20")],
            self.db.with_name("listed_stocks.json"),
        )

    def run_cli(self, start, end="2026-09-05"):
        output = io.StringIO()
        argv = [
            "run_portfolio",
            "--config",
            str(self.config),
            "--start",
            start,
            "--end",
            end,
            "--export",
            "--output-dir",
            str(self.root / "reports"),
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            code = run_portfolio.main()
        return code, output.getvalue()

    def report(self):
        (folder,) = (self.root / "reports").iterdir()
        return (
            json.loads((folder / "results.json").read_text(encoding="utf-8")),
            json.loads((folder / "input_data.json").read_text(encoding="utf-8")),
        )

    def test_listing_date_is_compared_with_the_start(self):
        self.write_listing()
        self.assertEqual(
            listing_exclusions(["3054", "2330", "9999"], self.db, "2026-08-19"),
            [
                {
                    "stock_id": "3054",
                    "listed_date": "2026-08-20",
                    "reason": "2026-08-20 上市，晚於回測開始日 2026-08-19，不納入股票池",
                }
            ],
        )
        # 上市當天開始的回測照常納入；不在名單上的代號不排除。
        self.assertEqual(listing_exclusions(["3054", "9999"], self.db, "2026-08-20"), [])

    def test_without_a_listing_file_nothing_is_excluded(self):
        self.assertEqual(listing_exclusions(["3054"], self.db, "2026-08-10"), [])
        code, output = self.run_cli("2026-08-10")
        # 沒有上市日可判斷：上市前的日子是原因未確認的缺列，結果不完整（退出碼 2）。
        self.assertEqual(code, 2, output)
        self.assertIn("原因未確認的缺列：1 檔", output)
        self.assertIn("3054 | 2026-08-10 ～ 2026-08-19 | 10 個行情日", output)

    def test_comparison_and_selection_report_unconfirmed_gaps(self):
        # 沒有上市名單：3054 上市前的日子是原因未確認的缺列，策略比較與選股評估都標示不完整。
        from tw_stock_backtest.cli import compare_strategies as compare_cli
        from tw_stock_backtest.cli import evaluate_selection

        strategies = [
            from_settings(self.settings),
            from_settings(self.settings, strategy_id="other", name="另一份"),
        ]
        folder = compare_strategies(
            self.settings, strategies, "2026-08-10", "2026-09-05", self.root / "single"
        )
        common = json.loads((folder / "common.json").read_text(encoding="utf-8"))["execution"]
        self.assertEqual([g["stock_id"] for g in common["unconfirmed_gaps"]], ["3054"])

        paths = []
        for strategy in strategies:
            path = self.root / f"{strategy['id']}.json"
            path.write_text(json.dumps(strategy, ensure_ascii=False), encoding="utf-8")
            paths.append(str(path))
        with redirect_stdout(io.StringIO()):
            code = compare_cli.main(
                [
                    "--config",
                    str(self.config),
                    "--strategies",
                    *paths,
                    "--start",
                    "2026-08-10",
                    "--end",
                    "2026-09-05",
                    "--output-dir",
                    str(self.root / "cli"),
                ]
            )
        self.assertEqual(code, 2)

        output = io.StringIO()
        argv = [
            "evaluate_selection",
            "--config",
            str(self.config),
            "--start",
            "2026-08-10",
            "--end",
            "2026-09-05",
            "--horizons",
            "1",
            "--top",
            "1",
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            code = evaluate_selection.main()
        self.assertEqual(code, 2, output.getvalue())
        self.assertIn("原因未確認的缺列：3054 | 2026-08-10 ～ 2026-08-19", output.getvalue())

    def test_synthetic_listing_has_no_dates_and_excludes_nothing(self):
        stock = {"stock_id": "3054", "name": "合成", "market": "合成示例", "industry": "示例"}
        save_synthetic_universe(
            [stock], self.db.with_name("listed_stocks.json"), source_url="synthetic://test"
        )
        self.assertEqual(listing_exclusions(["3054"], self.db, "2026-08-10"), [])

    def test_backtest_excludes_late_listings_and_records_why(self):
        self.write_listing()
        code, output = self.run_cli("2026-08-10")
        self.assertEqual(code, 0, output)
        self.assertIn("股票池：2330\n", output)
        self.assertIn("未納入股票池：3054（2026-08-20 上市，晚於回測開始日 2026-08-10", output)
        results, inputs = self.report()
        self.assertEqual(
            [e["stock_id"] for e in results["strategy"]["result"]["universe_exclusions"]],
            ["3054"],
        )
        self.assertEqual(list(inputs["records_by_stock"]), ["2330"])

    def test_backtest_starting_on_the_listing_date_keeps_the_stock(self):
        self.write_listing()
        code, output = self.run_cli("2026-08-20")
        self.assertEqual(code, 0, output)
        results, inputs = self.report()
        self.assertEqual(results["strategy"]["result"]["universe_exclusions"], [])
        self.assertEqual(sorted(inputs["records_by_stock"]), ["2330", "3054"])

    def test_all_stocks_listed_after_the_start_fail(self):
        save_universe(
            [listed("2330", "2026-09-01"), listed("3054", "2026-09-01")],
            self.db.with_name("listed_stocks.json"),
        )
        code, output = self.run_cli("2026-08-10")
        self.assertEqual(code, 1)
        self.assertIn("股票池在回測開始日都還沒上市", output)

    def test_comparison_excludes_late_listings_per_period(self):
        self.write_listing()
        strategies = [
            from_settings(self.settings),
            from_settings(self.settings, strategy_id="other", name="另一份"),
        ]
        folder = compare_strategies(
            self.settings, strategies, "2026-08-10", "2026-09-05", self.root / "single"
        )
        common = json.loads((folder / "common.json").read_text(encoding="utf-8"))["execution"]
        self.assertEqual(common["stocks"], ["2330"])
        self.assertEqual([e["stock_id"] for e in common["universe_exclusions"]], ["3054"])

        def period(pid, start):
            return dict(
                id=pid, start=start, end="2026-09-05", purpose="development", previously_seen=True
            )

        batch, code = compare_periods(
            self.settings,
            strategies,
            [period("before", "2026-08-10"), period("after", "2026-08-21")],
            self.root / "batch",
        )
        # 測試行情太短，因子有無法評估的日子（partial）；重點是兩段都沒有因缺列失敗。
        self.assertIn(code, (0, 2))
        data = json.loads((batch / "comparison.json").read_text(encoding="utf-8"))
        self.assertNotIn("failed", [p["status"] for p in data["periods"]])
        stocks = [
            json.loads((batch / p["report"] / "common.json").read_text(encoding="utf-8"))[
                "execution"
            ]["stocks"]
            for p in data["periods"]
        ]
        self.assertEqual(stocks, [["2330"], ["2330", "3054"]])


class ExclusionPageTests(PageCase):
    def test_backtest_page_lists_stocks_not_in_the_universe(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        results["strategy"]["result"] = {
            "universe_exclusions": [
                {
                    "stock_id": "3054",
                    "listed_date": "2026-09-01",
                    "reason": "2026-09-01 上市，晚於回測開始日 2026-08-03，不納入股票池",
                }
            ]
        }
        write(folder, "results.json", results)
        key = next(iter(self.workbench.catalog()))
        text = self.client.get(f"/reports/{key}").text
        self.assertIn("<h2>未納入股票池</h2>", text)
        self.assertIn("2026-09-01 上市，晚於回測開始日 2026-08-03", text)

    def test_backtest_page_lists_unconfirmed_gaps(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        results["strategy"]["result"] = {
            "unconfirmed_gaps": [
                {"stock_id": "4155", "first": "2026-10-07", "last": "2026-10-14", "days": 6}
            ],
            "market_events": [
                {
                    "stock_id": "4155",
                    "date": "2026-10-07",
                    "market_status": "MISSING_ROW",
                    "reason": "缺少行情列（原因未確認，不認定為停牌）",
                }
            ],
        }
        write(folder, "results.json", results)
        key = next(iter(self.workbench.catalog()))
        text = self.client.get(f"/reports/{key}").text
        self.assertIn("<h2>原因未確認的缺列</h2>", text)
        self.assertIn("缺少行情列</span>", text)


if __name__ == "__main__":
    unittest.main()
