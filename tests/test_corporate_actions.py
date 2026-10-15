"""公司行動的解析與儲存；樣本是證交所原始回應（見 fixtures/corporate_actions/README.md）。"""

import json
import sqlite3
import unittest
from contextlib import closing
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import SCHEMA_VERSION, migrate_database, save_records

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "corporate_actions"
LIST_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def dividends(stocks):
    return {
        (action["stock_id"], action["effective_date"]): action
        for row, key, needs_detail in ca.dividend_rows(fixture("twt49u.json"), stocks)
        for action in [
            ca.parse_dividend(
                row,
                fixture(f"twt49u-detail-{key[0]}.json") if needs_detail else None,
                list_url=LIST_URL,
            )
        ]
    }


class ValueParsingTests(unittest.TestCase):
    def test_roc_dates_and_amounts(self):
        self.assertEqual(ca.roc_date("115年03月17日"), "2026-03-17")
        self.assertEqual(ca.roc_date("113/11/11"), "2024-11-11")
        self.assertEqual(ca.amount("1,845.00"), D("1845.00"))
        self.assertEqual(ca.amount("6.000035 元／股"), D("6.000035"))
        self.assertEqual(ca.amount("861.00000000 股"), D("861.00000000"))
        self.assertIsNone(ca.amount("--"))
        with self.assertRaises(ValueError):
            ca.amount("abc")
        with self.assertRaises(ValueError):
            ca.roc_date("115-03")

    def test_failed_query_and_changed_columns_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "未成功"):
            ca.records({"stat": "很抱歉，沒有符合條件的資料!"})
        with self.assertRaisesRegex(ValueError, "欄位數量"):
            ca.records({"stat": "OK", "fields": ["a", "b"], "data": [["1"]]})
        with self.assertRaisesRegex(ValueError, "唯一的欄位"):
            ca._field({"權/息": "息", "權值+息值": "1"}, "權")


class DividendTests(unittest.TestCase):
    def test_cash_dividend_without_detail(self):
        actions = dividends({"2330", "2317", "2454"})
        self.assertEqual(len(actions), 6)
        tsmc = actions[("2330", "2026-03-17")]
        self.assertEqual(
            (tsmc["kind"], tsmc["previous_close"], tsmc["reference_price"], tsmc["cash_dividend"]),
            (ca.DIVIDEND, D("1845.00"), D("1838.99"), D("6.000035")),
        )
        self.assertEqual(tsmc["stock_dividend_per_1000"], 0)
        self.assertFalse(tsmc["needs_review"], tsmc["review_notes"])
        self.assertEqual(actions[("2317", "2026-07-02")]["cash_dividend"], D("7.171792"))

    def test_stock_dividend_uses_detail_and_matches_the_reference_price(self):
        rows = ca.dividend_rows(fixture("twt49u.json"), {"6592"})
        self.assertEqual([needs for _, _, needs in rows], [True])
        action = dividends({"6592"})[("6592", "2026-06-30")]
        self.assertEqual(
            (action["cash_dividend"], action["stock_dividend_per_1000"]), (D("3"), D("30"))
        )
        # (64 − 3) ÷ 1.03 = 59.22，與官方參考價一致。
        self.assertFalse(action["needs_review"], action["review_notes"])
        self.assertTrue(any("TWT49UDetail" in url for url in action["sources"]))
        with self.assertRaisesRegex(ValueError, "需要詳細資料"):
            ca.parse_dividend(rows[0][0], None, list_url=LIST_URL)

    def test_other_stocks_and_etfs_are_left_out(self):
        self.assertEqual(ca.dividend_rows(fixture("twt49u.json"), {"9999"}), [])
        self.assertEqual(len(dividends({"2330"})), 3)

    def test_mismatched_reference_price_needs_review(self):
        payload = fixture("twt49u.json")
        for row in payload["data"]:
            if row[1].strip() == "2330":
                row[4] = "1,800.00"
        row, _, _ = ca.dividend_rows(payload, {"2330"})[0]
        action = ca.parse_dividend(row, list_url=LIST_URL)
        self.assertTrue(action["needs_review"])
        self.assertIn("與官方 1800.00 不符", action["review_notes"][0])


class ReductionTests(unittest.TestCase):
    def parse(self, stock_id, effective_date):
        rows, combined = ca.reduction_rows(fixture("twtauu.json"), {stock_id})
        for row, _ in rows:
            if ca.roc_date(row[ca._key("恢復買賣日期")]) == effective_date:
                detail = fixture(f"twtavu-detail-{stock_id}.json")
                return ca.parse_reduction(
                    row, detail, list_url=LIST_URL, combined_dividend=combined.get(stock_id)
                )
        raise AssertionError("找不到樣本列")

    def test_loss_offset_reduction(self):
        action = self.parse("1441", "2026-09-29")
        self.assertEqual(action["kind"], ca.CAPITAL_REDUCTION)
        self.assertEqual(
            (action["previous_close"], action["reference_price"]), (D("8.72"), D("10.13"))
        )
        self.assertEqual(
            (action["shares_per_1000"], action["cash_return"]), (D("861.00000000"), D("0.000000"))
        )
        self.assertEqual(action["halt_start"], "2026-09-17")
        self.assertFalse(action["needs_review"], action["review_notes"])

    def test_note_dividend_not_matching_this_reduction_is_flagged_not_assigned(self):
        # 注意事項列出 3356 併案股利 2.9 元，但 2026-09 這次的官方參考價不含它。
        rows, combined = ca.reduction_rows(fixture("twtauu.json"), {"3356"})
        self.assertEqual(combined, {"3356": D("2.900000")})
        self.assertEqual(len(rows), 2)
        action = self.parse("3356", "2026-09-21")
        self.assertEqual(action["cash_dividend"], 0)
        self.assertEqual(action["cash_return"], D("1.488825"))
        self.assertTrue(action["needs_review"])
        self.assertIn("可能屬於同一查詢期間的另一次減資", action["review_notes"][0])

    def test_note_dividend_matching_the_reference_price_is_assigned_and_flagged(self):
        rows, combined = ca.reduction_rows(fixture("twtauu.json"), {"2323"})
        row, _ = rows[0]
        detail = {
            "stat": "OK",
            "fields": [
                "股票代號：",
                "股票名稱：",
                "停止買賣日期：",
                "每壹仟股換發新股票：",
                "每股退還股款：",
                "原股每股配發現金股利：",
                "減資並(有償)現金增資：",
                "每股認購金額：",
                "a. 公開承銷：",
                "b. 員工認購：",
                "c. 原股東認購：",
                "按股東持股比例每千股認購：",
            ],
            # 2323 的詳細資料數值是 2026-10-15 查詢 TWTAVUDetail 所得（每千股換發 900 股、每股退還 1 元），
            # 依同一端點的欄位格式組成。
            "data": [
                [
                    "2323  ",
                    "中環",
                    "115/10/01",
                    "900.00000000 股",
                    "1.000000 元/股",
                    "0 元/股",
                    "0 股",
                    "0 元/股",
                    "0 股",
                    "0 股",
                    "0 股",
                    "0 股",
                ]
            ],
        }
        action = ca.parse_reduction(
            row, detail, list_url=LIST_URL, combined_dividend=combined["2323"]
        )
        self.assertEqual(action["cash_dividend"], D("0.200000"))
        self.assertTrue(action["needs_review"])
        self.assertIn("含股利的公式與官方參考價吻合", action["review_notes"][0])

    def test_mismatched_detail_is_rejected(self):
        rows, _ = ca.reduction_rows(fixture("twtauu.json"), {"1441"})
        with self.assertRaisesRegex(ValueError, "詳細資料不符"):
            ca.parse_reduction(rows[0][0], fixture("twtavu-detail-3356.json"), list_url=LIST_URL)

    def test_halt_consistency(self):
        action = self.parse("1441", "2026-09-29")
        ca.check_halts([action], {("1441", "2026-09-17"): {}})
        self.assertFalse(action["needs_review"])
        ca.check_halts([action], {})
        self.assertIn("不在已確認停牌清單中", action["review_notes"][-1])


class ParChangeTests(unittest.TestCase):
    def test_ratio_from_reference_price(self):
        actions = {
            row["股票代號"].strip(): ca.parse_par_change(row, list_url=LIST_URL)
            for row in ca.par_change_rows(fixture("twtb8u.json"), {"8476", "4763"})
        }
        self.assertEqual(actions["8476"]["shares_per_1000"], D("2000"))  # 58.80 → 29.40
        self.assertEqual(actions["4763"]["shares_per_1000"], D("10000"))  # 885 → 88.50
        self.assertEqual(actions["4763"]["effective_date"], "2025-06-30")
        self.assertFalse(any(a["needs_review"] for a in actions.values()))

    def test_unrecognised_ratio_needs_review(self):
        payload = fixture("twtb8u.json")
        payload["data"][0][4] = "21.00"
        row = ca.par_change_rows(payload, {"8476"})[0]
        action = ca.parse_par_change(row, list_url=LIST_URL)
        self.assertIsNone(action["shares_per_1000"])
        self.assertTrue(action["needs_review"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "stocks.db"

    def test_round_trip_keeps_decimal_text_and_notes(self):
        actions = list(dividends({"2330", "6592"}).values())
        actions[0]["review_notes"] = ["測試備註"]
        actions[0]["needs_review"] = True
        self.assertEqual(
            ca.save_actions(actions, self.db, fetched_at="2026-10-15T00:00:00+00:00"), 4
        )
        loaded = ca.load_actions(self.db)
        self.assertEqual(len(loaded), 4)
        by_key = {(a["stock_id"], a["effective_date"]): a for a in loaded}
        tsmc = by_key[("2330", "2026-03-17")]
        self.assertEqual(tsmc["cash_dividend"], D("6.000035"))
        self.assertEqual(str(tsmc["previous_close"]), "1845.00")
        self.assertEqual(
            by_key[(actions[0]["stock_id"], actions[0]["effective_date"])]["review_notes"],
            ["測試備註"],
        )
        self.assertEqual(
            ca.load_actions(self.db, "2330", "2026-06-01", "2026-12-31")[0]["effective_date"],
            "2026-06-11",
        )
        self.assertEqual(
            ca.summary(self.db), {"count": 4, "latest": "2026-09-16", "needs_review": 1}
        )

    def test_saving_again_updates_instead_of_duplicating(self):
        action = dividends({"2330"})[("2330", "2026-03-17")]
        ca.save_actions([action], self.db, fetched_at="t1")
        action["cash_dividend"] = D("6.5")
        ca.save_actions([action], self.db, fetched_at="t2")
        loaded = ca.load_actions(self.db)
        self.assertEqual(
            [(a["cash_dividend"], a["fetched_at"]) for a in loaded], [(D("6.5"), "t2")]
        )

    def test_existing_price_database_is_upgraded_without_touching_prices(self):
        row = {
            "stock_id": "2330",
            "date": "2026-03-16",
            "open": D("1845"),
            "high": D("1850"),
            "low": D("1840"),
            "close": D("1845"),
            "volume": 1,
            "turnover": 1,
            "trade_count": 1,
        }
        save_records([row], db_path=self.db)
        with closing(sqlite3.connect(self.db)) as connection:
            before = connection.execute("SELECT * FROM stock_prices").fetchall()
            connection.execute("DROP TABLE corporate_actions")
            connection.execute("PRAGMA user_version = 2")
        self.assertEqual(migrate_database(self.db), (2, SCHEMA_VERSION))
        ca.save_actions(list(dividends({"2330"}).values()), self.db, fetched_at="t")
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM stock_prices").fetchall(), before)

    def test_invalid_actions_are_rejected_and_missing_database_is_not_created(self):
        action = dividends({"2330"})[("2330", "2026-03-17")]
        with self.assertRaisesRegex(ValueError, "https"):
            ca.save_actions(
                [{**action, "sources": ["http://example.com"]}], self.db, fetched_at="t"
            )
        with self.assertRaisesRegex(ValueError, "未知的公司行動類型"):
            ca.save_actions([{**action, "kind": "split"}], self.db, fetched_at="t")
        self.assertFalse(self.db.exists())
        self.assertEqual(ca.load_actions(Path(self.db.parent) / "missing.db"), [])
        self.assertIsNone(ca.summary(Path(self.db.parent) / "missing.db"))
        self.assertFalse((Path(self.db.parent) / "missing.db").exists())


if __name__ == "__main__":
    unittest.main()
