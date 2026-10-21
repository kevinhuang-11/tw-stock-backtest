"""公司行動下載指令：請求上限、整批寫入與不連網；來源以官方回應樣本模擬。"""

import contextlib
import io
import json
import re
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.cli import fetch_corporate_actions as cli
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.universe import save_synthetic_universe, universe_path_for

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "corporate_actions"
LISTS = {"TWT49U": "twt49u.json", "TWTAUU": "twtauu.json", "TWTB8U": "twtb8u.json"}


def fake_source(url, *, timeout):
    """依網址回傳樣本；列表只回傳查詢期間內的列，模擬每年一段的查詢。"""
    for code, name in LISTS.items():
        if f"/{code}?" in url:
            payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            start = re.search(r"startDate=(\d{8})", url)[1]
            end = re.search(r"endDate=(\d{8})", url)[1]
            iso = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"  # noqa: E731
            payload["data"] = [
                row for row in payload["data"] if iso(start) <= ca.roc_date(row[0]) <= iso(end)
            ]
            return payload
    match = re.search(r"/(TWT49UDetail|TWTAVUDetail)\?STK_NO=(\d{4})", url)
    if match:
        prefix = "twt49u-detail" if match[1] == "TWT49UDetail" else "twtavu-detail"
        return json.loads((FIXTURES / f"{prefix}-{match[2]}.json").read_text(encoding="utf-8"))
    raise AssertionError(f"未預期的請求：{url}")


class FetchCorporateActionsTests(unittest.TestCase):
    STOCKS = ["2330", "6592", "1441", "8476"]

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(TEST_CONFIG, encoding="utf-8")
        self.db = load_config(self.config)["storage"]["database_path"]
        # 請求間隔與重試等待不實際等待。
        patcher = patch.object(cli.time, "sleep", Mock())
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *arguments, source=fake_source):
        output = io.StringIO()
        with (
            patch.object(cli, "get_json", Mock(side_effect=source)) as mock,
            contextlib.redirect_stdout(output),
        ):
            code = cli.main(["--config", str(self.config), *arguments])
        return code, output.getvalue(), mock

    def test_plan_lists_requests_without_network(self):
        code, output, mock = self.run_cli(
            "--stocks", "2330", "--start", "2025-06-01", "--end", "2026-03-31", "--plan"
        )
        self.assertEqual(code, 0)
        mock.assert_not_called()
        self.assertIn("列表請求 6 次", output)
        self.assertIn("startDate=20250601&endDate=20251231", output)
        self.assertIn("startDate=20260101&endDate=20260331", output)
        self.assertFalse(Path(self.db).exists())

    def test_fetch_saves_every_kind_and_counts_requests(self):
        code, output, mock = self.run_cli(
            "--stocks", *self.STOCKS, "--start", "2024-01-01", "--end", "2026-10-15"
        )
        self.assertEqual(code, 0, output)
        # 3 年 × 3 個列表，加上 6592 配股與 1441 減資的詳細資料。
        self.assertEqual(mock.call_count, 11)
        self.assertIn("請求 11 次；寫入 6 筆公司行動", output)
        actions = {
            (a["stock_id"], a["effective_date"], a["kind"]): a for a in ca.load_actions(self.db)
        }
        self.assertEqual(
            sorted(actions),
            [
                ("1441", "2026-09-29", ca.CAPITAL_REDUCTION),
                ("2330", "2026-03-17", ca.DIVIDEND),
                ("2330", "2026-06-11", ca.DIVIDEND),
                ("2330", "2026-09-16", ca.DIVIDEND),
                ("6592", "2026-06-30", ca.DIVIDEND),
                ("8476", "2024-11-11", ca.PAR_CHANGE),
            ],
        )
        # 1441 的停止買賣首日在已確認停牌清單中，不需要人工確認。
        self.assertFalse(actions[("1441", "2026-09-29", ca.CAPITAL_REDUCTION)]["needs_review"])
        self.assertEqual(
            actions[("6592", "2026-06-30", ca.DIVIDEND)]["stock_dividend_per_1000"], D("30")
        )
        # 每檔都記錄已下載的期間，包括沒有任何公司行動的股票。
        self.assertEqual(
            ca.load_coverage(self.db),
            {stock: [("2024-01-01", "2026-10-15")] for stock in self.STOCKS},
        )

    def test_blank_prices_are_listed_for_review_and_the_rest_is_saved(self):
        # 原本前收或參考價空白時，算式對 None 運算丟出 TypeError，整批下載失敗。
        blanks = {
            ("TWT49U", "2330", "115年06月11日"): 3,  # 除權息前收盤價
            ("TWTAUU", "1441", "115/09/29"): 4,  # 恢復買賣參考價
            ("TWTB8U", "8476", "113/11/11"): 3,  # 停止買賣前收盤價格
        }

        def blank_source(url, *, timeout):
            payload = fake_source(url, timeout=timeout)
            for (code, stock, day), column in blanks.items():
                if f"/{code}?" in url:
                    for row in payload["data"]:
                        if row[1] == stock and row[0] == day:
                            row[column] = ""
            return payload

        code, output, _ = self.run_cli(
            "--stocks",
            *self.STOCKS,
            "--start",
            "2024-01-01",
            "--end",
            "2026-10-15",
            source=blank_source,
        )
        self.assertEqual(code, 2, output)
        saved = sorted((a["stock_id"], a["effective_date"]) for a in ca.load_actions(self.db))
        self.assertEqual(
            saved, [("2330", "2026-03-17"), ("2330", "2026-09-16"), ("6592", "2026-06-30")]
        )
        self.assertIn("需要人工確認、未寫入：3 筆", output)
        self.assertIn("2026-06-11 | 2330 | 除權息 | 前收空白", output)
        self.assertIn("2026-09-29 | 1441 | 減資 | 參考價空白", output)
        self.assertIn("2024-11-11 | 8476 | 變更面額 | 前收空白", output)
        # 有未寫入的列的股票不記錄已下載期間，報表會列為未涵蓋，不當成沒有公司行動。
        self.assertEqual(sorted(ca.load_coverage(self.db)), ["6592"])

    def test_parsers_raise_a_value_error_for_blank_prices(self):
        payload = json.loads((FIXTURES / "twt49u.json").read_text(encoding="utf-8"))
        payload["data"] = [payload["data"][1]]
        payload["data"][0][4] = "--"  # 除權息參考價
        (row,) = ca.records(payload)
        with self.assertRaises(ca.IncompleteSourceRow) as caught:
            ca.parse_dividend(row, list_url="https://www.twse.com.tw/x")
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(caught.exception.item["missing"], ["參考價"])
        self.assertEqual(caught.exception.item["stock_id"], "2330")

    def test_budget_is_checked_before_details_and_nothing_is_saved(self):
        code, output, mock = self.run_cli(
            "--stocks",
            *self.STOCKS,
            "--start",
            "2024-01-01",
            "--end",
            "2026-10-15",
            "--max-requests",
            "10",
        )
        self.assertEqual(code, 1)
        self.assertIn("詳細資料還需要 2 次", output)
        self.assertEqual(mock.call_count, 9)
        self.assertEqual(ca.load_actions(self.db), [])

    def test_retries_count_toward_the_budget(self):
        calls = {"n": 0}

        def flaky(url, *, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(url, 503, "busy", None, None)
            return fake_source(url, timeout=timeout)

        code, output, _ = self.run_cli(
            "--stocks", "2330", "--start", "2026-01-01", "--end", "2026-10-15", source=flaky
        )
        self.assertEqual(code, 0, output)
        self.assertIn("請求 4 次；寫入 3 筆", output)

    def test_failed_request_saves_nothing(self):
        def broken(url, *, timeout):
            if "TWTB8U" in url:
                raise HTTPError(url, 500, "error", None, None)
            return fake_source(url, timeout=timeout)

        code, output, _ = self.run_cli(
            "--stocks", "2330", "--start", "2026-01-01", "--end", "2026-10-15", source=broken
        )
        self.assertEqual(code, 1)
        self.assertIn("公司行動下載失敗", output)
        self.assertEqual(ca.load_actions(self.db), [])
        self.assertEqual(ca.load_coverage(self.db), {})

    def test_synthetic_universe_never_connects(self):
        save_synthetic_universe(
            [
                {
                    "stock_id": "9001",
                    "name": "示範股 A（合成）",
                    "industry": "示範產業甲",
                    "market": "合成示例",
                }
            ],
            universe_path_for(self.db),
            source_url="synthetic://offline-example",
        )
        code, output, mock = self.run_cli(
            "--stocks", "9001", "--start", "2026-01-01", "--end", "2026-10-15"
        )
        self.assertEqual(code, 1)
        self.assertIn("未連網", output)
        mock.assert_not_called()

    def test_invalid_arguments(self):
        for extra in (["--stocks", "23300"], ["--max-requests", "0"]):
            with self.subTest(extra):
                code, output, mock = self.run_cli(
                    "--start", "2026-01-01", "--end", "2026-10-15", *extra
                )
                self.assertEqual(code, 1)
                mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
