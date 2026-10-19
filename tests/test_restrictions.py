"""處置股與全額交割（docs/HALT-POLICY.md 3.17、3.18）：解析、儲存、合併期間與標示。

樣本依證交所「公布處置有價證券」與「變更交易」回應的欄位格式組成；全部離線。
"""

import contextlib
import io
import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from test_ui_pages import PageCase, write

from tw_stock_backtest.cli import fetch_restrictions
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data import restrictions as tr
from tw_stock_backtest.data.database import save_records

PUNISH_FIELDS = [
    "編號",
    "公布日期",
    "證券代號",
    "證券名稱",
    "累計",
    "處置條件",
    "處置起迄時間",
    "處置措施",
    "處置內容",
    "備註",
]


def punish(*rows):
    return {"stat": "OK", "fields": PUNISH_FIELDS, "data": [list(r) for r in rows]}


def punish_row(stock_id, announced, period, measure="第一次處置"):
    return (1, announced, stock_id, "測試", 1, "連續三次", period, measure, "處置內容", "")


def twt85u(day, *rows):
    return {
        "stat": "OK",
        "date": day.replace("-", ""),
        "fields": ["證券代號", "證券名稱", "分盤集合競價(以**表示)"],
        "data": [list(r) for r in rows],
    }


SOURCE = "https://www.twse.com.tw/rwd/zh/test"
DAYS = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]


class ParseTests(unittest.TestCase):
    def test_dispositions_keep_stocks_and_parse_the_period(self):
        entries = tr.parse_dispositions(
            punish(
                punish_row("2466", "115/10/13", "115/10/14～115/10/20", "第二次處置"),
                punish_row("053040", "115/10/14", "115/10/15～115/10/21"),  # 權證不保留
            ),
            source=SOURCE,
        )
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(
            (entry["stock_id"], entry["start_date"], entry["end_date"], entry["announced"]),
            ("2466", "2026-10-14", "2026-10-20", "2026-10-13"),
        )
        self.assertEqual(entry["measure"], "第二次處置")

    def test_bad_period_is_rejected(self):
        with self.assertRaises(ValueError):
            tr.parse_dispositions(
                punish(punish_row("2466", "115/10/13", "115/10/14")), source=SOURCE
            )

    def test_full_delivery_list_marks_split_auction(self):
        entries = tr.parse_full_delivery(
            twt85u("2026-08-10", ("2314", "台揚", "**"), ("1213", "大飲", "  ")),
            "2026-08-10",
            source=SOURCE,
        )
        measures = {e["stock_id"]: e["measure"] for e in entries}
        self.assertEqual(measures["2314"], "變更交易方法（全額交割），分盤集合競價")
        self.assertEqual(measures["1213"], "變更交易方法（全額交割）")
        with self.assertRaisesRegex(ValueError, "日期不符"):
            tr.parse_full_delivery(twt85u("2026-08-11"), "2026-08-10", source=SOURCE)


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "stocks.db"

    def save(self):
        entries = []
        # 1213 連續三天在名單；2314 第 1、3 天在名單（第 2 天不在），所以是兩段。
        lists = {
            DAYS[0]: [("1213", "大飲", ""), ("2314", "台揚", "**")],
            DAYS[1]: [("1213", "大飲", "")],
            DAYS[2]: [("1213", "大飲", ""), ("2314", "台揚", "**")],
        }
        for day, rows in lists.items():
            entries += tr.parse_full_delivery(twt85u(day, *rows), day, source=SOURCE)
        entries += tr.parse_dispositions(
            punish(punish_row("2466", "115/08/04", "115/08/05～115/08/11")), source=SOURCE
        )
        coverage = [(tr.FULL_DELIVERY, day, day) for day in lists]
        coverage.append((tr.DISPOSITION, "2026-08-01", "2026-08-31"))
        tr.save_restrictions(entries, self.db, fetched_at="t", coverage=coverage)

    def test_daily_lists_are_merged_into_consecutive_periods(self):
        self.save()
        loaded = tr.load_restrictions(self.db)
        self.assertEqual(
            [(i["start_date"], i["end_date"]) for i in loaded["1213"]], [(DAYS[0], DAYS[2])]
        )
        self.assertEqual(
            [(i["start_date"], i["end_date"]) for i in loaded["2314"]],
            [(DAYS[0], DAYS[0]), (DAYS[2], DAYS[2])],
        )
        self.assertEqual(loaded["2466"][0]["label"], "處置（第一次處置，2026-08-05 ～ 2026-08-11）")
        summary = tr.coverage_summary(self.db)
        self.assertEqual(summary["full_delivery"], {"first": DAYS[0], "last": DAYS[2], "days": 3})
        self.assertEqual(summary["disposition"], [["2026-08-01", "2026-08-31"]])

    def test_active_labels_only_after_the_announcement_and_within_the_period(self):
        self.save()
        loaded = tr.load_restrictions(self.db)
        self.assertEqual(tr.active(loaded, "2466", "2026-08-04"), [])  # 公布日，尚未生效
        self.assertEqual(len(tr.active(loaded, "2466", "2026-08-05")), 1)
        self.assertEqual(tr.active(loaded, "2466", "2026-08-12"), [])
        self.assertEqual(tr.active(loaded, "2314", DAYS[1]), [])

    def test_missing_tables_or_database_mean_nothing_is_known(self):
        self.assertEqual(tr.load_restrictions(self.db), {})
        self.assertIsNone(tr.coverage_summary(self.db))

    def test_labels_do_not_change_numbers(self):
        self.save()
        loaded = tr.load_restrictions(self.db)
        result = {
            "trades": [{"stock_id": "1213", "date": DAYS[1], "price": Decimal("10")}],
            "final_positions": [{"stock_id": "2314", "shares": 1, "value": Decimal("5")}],
            "equity_curve": [{"date": DAYS[2]}],
        }
        before = json.dumps(result, default=str)
        tr.annotate_backtest(result, loaded)
        self.assertEqual(len(result["trades"][0]["restrictions"]), 1)
        self.assertIn("分盤集合競價", result["final_positions"][0]["restrictions"][0])
        for item in (result["trades"][0], result["final_positions"][0]):
            item.pop("restrictions")
        self.assertEqual(json.dumps(result, default=str), before)
        rows = tr.annotate_rankings([{"stock_id": "1213"}, {"stock_id": "2330"}], loaded, DAYS[0])
        self.assertIn("restrictions", rows[0])
        self.assertNotIn("restrictions", rows[1])


class FetchCliTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(TEST_CONFIG, encoding="utf-8")
        self.db = Path(load_config(self.config)["storage"]["database_path"])
        rows = [
            {
                "stock_id": "1213",
                "date": day,
                "open": Decimal("10"),
                "high": Decimal("10"),
                "low": Decimal("10"),
                "close": Decimal("10"),
                "volume": 1,
                "turnover": 1,
                "trade_count": 1,
            }
            for day in DAYS
        ]
        save_records(rows, db_path=self.db)

    def run_cli(self, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = fetch_restrictions.main(
                [
                    "--config",
                    str(self.config),
                    "--start",
                    "2026-08-01",
                    "--end",
                    "2026-08-31",
                    *extra,
                ]
            )
        return code, output.getvalue()

    def test_plan_counts_one_request_per_local_market_day(self):
        with patch.object(fetch_restrictions.Client, "get", side_effect=AssertionError("連網")):
            code, output = self.run_cli("--plan")
        self.assertEqual(code, 0)
        self.assertIn("全額交割：4 個行情日（2026-08-03 ～ 2026-08-06）", output)
        self.assertIn("合計 5 次請求", output)

    def test_over_budget_does_not_connect_or_write(self):
        with patch.object(fetch_restrictions.Client, "get", side_effect=AssertionError("連網")):
            code, output = self.run_cli("--max-requests", "4")
        self.assertEqual(code, 1)
        self.assertIn("超過上限 4 次", output)
        self.assertIsNone(tr.coverage_summary(self.db))

    def test_download_writes_everything_or_nothing(self):
        def fake(url):
            if "punish" in url:
                return punish(punish_row("1213", "115/08/03", "115/08/04～115/08/10"))
            day = url.split("date=")[1][:8]
            return twt85u(f"{day[:4]}-{day[4:6]}-{day[6:]}", ("1213", "大飲", ""))

        with patch.object(
            fetch_restrictions.Client, "get", side_effect=lambda self, url: fake(url), autospec=True
        ):
            code, output = self.run_cli()
        self.assertEqual(code, 0, output)
        loaded = tr.load_restrictions(self.db)
        self.assertEqual(
            [(i["kind"], i["start_date"], i["end_date"]) for i in loaded["1213"]],
            [("full_delivery", DAYS[0], DAYS[3]), ("disposition", "2026-08-04", "2026-08-10")],
        )

        # 中途失敗：前面成功的部分也不寫入。
        other = self.root / "other.toml"
        other.write_text(TEST_CONFIG.replace("data/stocks.db", "data/other.db"), encoding="utf-8")
        save_records(
            [
                {**r, "date": d}
                for r in [
                    {
                        "stock_id": "1213",
                        "open": Decimal("1"),
                        "high": Decimal("1"),
                        "low": Decimal("1"),
                        "close": Decimal("1"),
                        "volume": 1,
                        "turnover": 1,
                        "trade_count": 1,
                    }
                ]
                for d in DAYS
            ],
            db_path=self.root / "data" / "other.db",
        )

        def flaky(self_, url):
            if "20260805" in url:
                raise ValueError("回應格式錯誤")
            return fake(url)

        with patch.object(fetch_restrictions.Client, "get", side_effect=flaky, autospec=True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = fetch_restrictions.main(
                    ["--config", str(other), "--start", "2026-08-01", "--end", "2026-08-31"]
                )
        self.assertEqual(code, 1)
        self.assertEqual(tr.load_restrictions(self.root / "data" / "other.db"), {})


class ReportPageTests(PageCase):
    def test_trades_are_labelled_and_coverage_is_stated(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        results["strategy"]["result"] = {
            "trades": [
                {
                    "stock_id": "2466",
                    "date": "2026-10-15",
                    "signal_date": "2026-10-14",
                    "action": "BUY",
                    "price": "10",
                    "quantity": 1,
                    "commission": "0",
                    "tax": "0",
                    "cash_after": "0",
                    "restrictions": ["處置（第二次處置，2026-10-14 ～ 2026-10-20）"],
                }
            ],
            "restriction_coverage": {
                "disposition": [["2026-08-01", "2026-10-14"]],
                "full_delivery": {"first": "2026-08-03", "last": "2026-10-14", "days": 50},
            },
        }
        write(folder, "results.json", results)
        (folder / "trades.csv").write_text(
            "source,stock_id,signal_date,date,action,price,quantity,amount,commission,tax,"
            "cash_change,cash_after\n"
            "strategy,2466,2026-10-14,2026-10-15,BUY,10,1,10,0,0,-10,0\n",
            encoding="utf-8-sig",
        )
        key = next(iter(self.workbench.catalog()))
        text = self.client.get(f"/reports/{key}").text
        self.assertIn("處置（第二次處置，2026-10-14 ～ 2026-10-20）</span>", text)
        self.assertIn("全額交割名單 2026-08-03 ～ 2026-10-14（50 個行情日）", text)

    def test_old_reports_say_nothing_was_labelled(self):
        self.add_backtest()
        key = next(iter(self.workbench.catalog()))
        self.assertIn("沒有下載資料，未標示", self.client.get(f"/reports/{key}").text)


if __name__ == "__main__":
    unittest.main()
