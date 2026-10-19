"""待人工確認的公司行動清單：依目前規則重新檢查、分類與列出影響。資料為合成，數值仿 2026 年官方案例。"""

import contextlib
import io
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_corporate_actions import LIST_URL, dividend_payload
from test_end_to_end import TEST_CONFIG
from test_ui_pages import PageCase

from tw_stock_backtest.cli import fetch_corporate_actions, inspect_db
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import save_records


def stale_1795():
    """依舊規則判斷為不符的 1795（已儲存的標記），目前規則下應解除。"""
    row, detail = dividend_payload(
        "1795",
        "115年08月31日",
        "193.00",
        "187.98",
        "5.010442",
        "權息",
        [
            "2.565316 元／股",
            "",
            "13 股",
            "0 股",
            "0 股",
            "0 元／股",
            "0 股",
            "0 股",
            "0 股",
            "0.00000000 股",
        ],
    )
    action = ca.parse_dividend(row, detail, list_url=LIST_URL)
    action["needs_review"] = True
    action["review_notes"] = ["依公告數值反推參考價為 187.99，與官方 187.98 不符"]
    return action


def rights_6225():
    row, detail = dividend_payload(
        "6225",
        "115年08月18日",
        "44.40",
        "30.04",
        "14.355834",
        "權",
        [
            "0 元／股",
            "",
            "0 股",
            "0 股",
            "32,200,000 股",
            "17.65 元／股",
            "3,220,000 股",
            "4,830,000 股",
            "24,150,000 股",
            "868.70503597 股",
        ],
    )
    return ca.parse_dividend(row, detail, list_url=LIST_URL)


def early_reduction():
    """仿 1459：恢復買賣日正好是本機第一筆行情，停止買賣首日不在已確認停牌清單中。"""
    return {
        "stock_id": "1459",
        "effective_date": "2026-08-03",
        "kind": ca.CAPITAL_REDUCTION,
        "previous_close": D("11.85"),
        "reference_price": D("12.46"),
        "cash_dividend": D(0),
        "stock_dividend_per_1000": D(0),
        "shares_per_1000": D("750"),
        "cash_return": D("2.5"),
        "rights_per_1000": D(0),
        "rights_price": D(0),
        "halt_start": "2026-07-23",
        "needs_review": True,
        "review_notes": ["停止買賣首日 2026-07-23 不在已確認停牌清單中"],
        "sources": ["https://www.twse.com.tw/example"],
        "raw": {"list": {}},
    }


def price(stock_id, day):
    return {
        "stock_id": stock_id,
        "date": day,
        "open": D(10),
        "high": D(10),
        "low": D(10),
        "close": D(10),
        "volume": 1,
        "turnover": 1,
        "trade_count": 1,
    }


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / "config.toml"
        self.config.write_text(TEST_CONFIG, encoding="utf-8")
        self.db = load_config(self.config)["storage"]["database_path"]
        save_records(
            [price("1795", "2026-08-03"), price("6225", "2026-08-03"), price("1459", "2026-08-03")],
            db_path=self.db,
        )
        ca.save_actions([stale_1795(), rights_6225(), early_reduction()], self.db, fetched_at="t")

    def items(self):
        return {i["stock_id"]: i for i in ca.review_items(self.db, {})}

    def test_items_are_classified_with_reasons_and_impact(self):
        items = self.items()
        self.assertEqual(items["1795"]["status"], ca.CLEARED)
        self.assertEqual(items["1459"]["status"], ca.NOT_APPLICABLE)
        self.assertIn("第一筆行情（2026-08-03）", items["1459"]["impact"])
        self.assertEqual(items["6225"]["status"], ca.PENDING)
        self.assertIn("回測持有跨過時拒絕計算", items["6225"]["impact"])
        self.assertIn("decline（放棄認購）", items["6225"]["impact"])

    def test_inspect_db_lists_the_review_items(self):
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["inspect_db", "--config", str(self.config), "--stock", "1795", "--review"],
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(inspect_db.main(), 0)
        text = output.getvalue()
        self.assertIn("待確認 1 筆，不適用 1 筆，依目前規則已解除 1 筆", text)
        self.assertIn("2026-08-18 | 6225 | 除權息 | 待確認", text)
        self.assertIn("已儲存的原因：依公告數值反推參考價為 187.99", text)

    def test_recheck_updates_only_the_flags(self):
        before = {a["stock_id"]: a for a in ca.load_actions(self.db)}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                fetch_corporate_actions.main(["--config", str(self.config), "--recheck"]), 0
            )
        self.assertIn("更新 1 筆", output.getvalue())
        after = {a["stock_id"]: a for a in ca.load_actions(self.db)}
        self.assertFalse(after["1795"]["needs_review"])
        for column in (
            "cash_dividend",
            "stock_dividend_per_1000",
            "reference_price",
            "sources",
            "raw",
        ):
            self.assertEqual(after["1795"][column], before["1795"][column], column)
        # 已儲存與目前規則都不需確認，清單中不再列出。
        self.assertNotIn("1795", self.items())
        self.assertEqual(self.items()["6225"]["status"], ca.PENDING)


class HomePageTests(PageCase):
    def test_data_status_shows_pending_reviews(self):
        db = self.workbench.settings["storage"]["database_path"]
        save_records([price("6225", "2026-08-03")], db_path=db)
        ca.save_actions([rights_6225()], db, fetched_at="t")
        text = self.client.get("/").text
        self.assertIn("待人工確認 1 筆", text)
        self.assertIn("inspect_db --review", text)


if __name__ == "__main__":
    unittest.main()
