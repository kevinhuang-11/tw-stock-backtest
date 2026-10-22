"""CLI 開頭與回測報表頁的計算範圍說明：依價格口徑、現金增資與終止上市的估值方式，不寫死。"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_adjusted_research import DAYS, rows
from test_end_to_end import TEST_CONFIG
from test_rights_offerings import rights
from test_ui_pages import PageCase, write

from tw_stock_backtest.backtesting.delisting import final_valuation_note
from tw_stock_backtest.cli import evaluate_selection, run_portfolio
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.prices import backtest_scope, forward_return_scope

STOCKS = ["2317", "2454"]


class ScopeTextTests(unittest.TestCase):
    def test_backtest_scope_follows_the_mode_and_rights(self):
        raw = backtest_scope("raw")
        self.assertIn("報酬不含股利與股數變動", raw)
        for rights_mode, expected in (
            ("refuse", "持有跨過含現金增資的除權時拒絕計算"),
            ("decline", "含現金增資的除權一律放棄認購"),
        ):
            text = backtest_scope("adjusted", rights_mode)
            self.assertIn("現金股利與減資退還股款於生效日入帳", text)
            self.assertIn(expected, text)
            self.assertNotIn("不含股利", text)
        # 舊報表沒有記錄 rights，當時的行為就是 refuse。
        self.assertEqual(backtest_scope("adjusted"), backtest_scope("adjusted", "refuse"))

    def test_forward_return_scope_matches_the_calculation(self):
        self.assertIn("只計價格", forward_return_scope("raw"))
        self.assertIn("樣本不計算", forward_return_scope("adjusted", "refuse"))
        self.assertIn("視為放棄認購", forward_return_scope("adjusted", "decline"))
        for rights_mode in ("refuse", "decline"):
            self.assertIn("計入現金股利", forward_return_scope("adjusted", rights_mode))

    def test_final_valuation_follows_the_delisting_setting(self):
        self.assertIn("以最後收盤凍結估值", final_valuation_note("last_close"))
        self.assertIn("以 0 估值，壓力測試", final_valuation_note("zero"))


class CliScopeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.toml"
        self.db = self.root / "data" / "stocks.db"
        self.write_config()
        for stock_id in STOCKS:
            save_records(rows(stock_id), db_path=self.db)

    def write_config(self, extra=""):
        self.config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', f"stocks = {json.dumps(STOCKS)}") + extra,
            encoding="utf-8",
        )

    def add_rights_offering(self):
        action = {
            **rights(),
            "halt_start": None,
            "review_notes": [],
            "sources": ["https://www.twse.com.tw/example"],
            "raw": {},
        }
        ca.save_actions(
            [action],
            self.db,
            fetched_at="t",
            coverage=[(sid, "2026-01-01", "2026-12-31") for sid in STOCKS],
        )

    def run_cli(self, module, *extra):
        argv = [
            module.__name__,
            "--config",
            str(self.config),
            "--start",
            DAYS[40],
            "--end",
            DAYS[-1],
            *extra,
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            code = module.main()
        return code, output.getvalue()

    def header(self, output, lines=3):
        return "\n".join(output.splitlines()[:lines])

    def test_run_portfolio_header_in_raw_mode(self):
        self.write_config('\n[corporate_actions]\nmode = "raw"\n')
        code, output = self.run_cli(run_portfolio)
        self.assertEqual(code, 0, output)
        header = self.header(output)
        self.assertIn(backtest_scope("raw"), header)
        self.assertIn(final_valuation_note("last_close"), header)

    def test_run_portfolio_header_in_adjusted_mode(self):
        code, output = self.run_cli(run_portfolio)
        self.assertEqual(code, 0, output)
        header = self.header(output)
        self.assertIn(backtest_scope("adjusted", "refuse"), header)
        self.assertNotIn("未計股利", output)
        self.assertNotIn("不含股利", header)

    def test_run_portfolio_header_with_declined_rights_and_zero_valuation(self):
        self.add_rights_offering()
        self.write_config(
            '\n[corporate_actions]\nmode = "adjusted"\nrights = "decline"\n'
            '\n[halts]\ndelisted_valuation = "zero"\n'
        )
        code, output = self.run_cli(run_portfolio)
        self.assertEqual(code, 0, output)
        header = self.header(output)
        self.assertIn(backtest_scope("adjusted", "decline"), header)
        self.assertIn(final_valuation_note("zero"), header)

    def test_evaluate_selection_header_follows_the_mode(self):
        cases = (
            ('\n[corporate_actions]\nmode = "raw"\n', "raw", None),
            ("", "adjusted", "refuse"),
            (
                '\n[corporate_actions]\nmode = "adjusted"\nrights = "decline"\n',
                "adjusted",
                "decline",
            ),
        )
        for extra, mode, rights_mode in cases:
            with self.subTest(mode=mode, rights=rights_mode):
                self.write_config(extra)
                code, output = self.run_cli(evaluate_selection, "--horizons", "1", "--top", "1")
                self.assertIn(code, (0, 2), output)
                header = self.header(output)
                self.assertIn(forward_return_scope(mode, rights_mode), header)
                self.assertNotIn("未計費稅、滑價、股利與拆股", output)


class ReportPageScopeTests(PageCase):
    def key(self):
        return next(iter(self.workbench.catalog()))

    def test_raw_report_page_uses_the_cli_text(self):
        self.add_backtest()
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn(backtest_scope("raw"), text)

    def test_final_holdings_note_uses_the_cli_text(self):
        for mode in ("last_close", "zero"):
            with self.subTest(mode):
                folder = self.add_backtest(name=f"20260930T020000_000000Z_{mode[:4]:a<8}")
                results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
                results["strategy"]["result"] = {
                    "delisted_valuation": mode,
                    "final_positions": [
                        {
                            "stock_id": "2330",
                            "shares": 10,
                            "status": "NORMAL",
                            "price": "100",
                            "price_date": "2026-09-29",
                            "value": "1000",
                        }
                    ],
                }
                write(folder, "results.json", results)
                key = next(
                    k
                    for k, entry in self.workbench.catalog().items()
                    if entry["path"].name == folder.name
                )
                text = self.client.get(f"/reports/{key}").text
                self.assertIn(final_valuation_note(mode), text)


if __name__ == "__main__":
    unittest.main()
