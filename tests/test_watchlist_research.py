import copy
import json
import re
import sqlite3
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from test_end_to_end import TEST_CONFIG
from test_strategy_library import records
from test_supplemental import revenue

from tw_stock_backtest.cli.update_supplemental import update
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data.supplemental import Store, parse_revenue, parse_revenue_history
from tw_stock_backtest.data.universe import save_universe
from tw_stock_backtest.notifications import build_message, preview_message
from tw_stock_backtest.research_digest import load_digest, render_digest
from tw_stock_backtest.stock_research import Watchlist
from tw_stock_backtest.strategies import from_settings
from tw_stock_backtest.watchlist_research import (
    compare,
    filter_rows,
    observations,
    previous_snapshots,
    save_snapshot,
)
from tw_stock_backtest.workbench import Workbench, create_app


def historical(sid="2330", months=None, values=None):
    return {
        "info": {"data": {"code": sid}},
        "chart": {
            "revenue": {
                "categories": months or ["202501", "202511", "202512", "202601"],
                "series": [{"name": "月營收", "data": values or [100000, 90000, 100000, 120000]}],
            }
        },
    }


class WatchResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(TEST_CONFIG.replace("AAA", "2330"))
        self.wb = Workbench(self.config, self.root / "reports", self.root / "state")
        self.addCleanup(self.wb.close)
        self.settings = self.wb.settings
        self.store = Store(
            Path(self.settings["storage"]["database_path"]).with_name("supplemental.sqlite3")
        )
        save_records(records(count=90), db_path=self.settings["storage"]["database_path"])
        save_universe(
            [
                {
                    "stock_id": sid,
                    "name": "<script>名稱</script>",
                    "market": "上市",
                    "industry": "測試",
                    "listed_date": "2000-01-01",
                    "cfi_code": "ESVUFR",
                }
                for sid in ("2330", "2317")
            ],
            Path(self.settings["storage"]["database_path"]).with_name("listed_stocks.json"),
        )
        self.wb.library.save(from_settings(self.settings))
        self.app = create_app(self.wb)
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = re.search(
            'name="csrf" value="([^"]+)"', self.client.get("/new/research").text
        )[1]
        self.notes = Watchlist(self.wb.state / "private/watchlist.sqlite3")
        self.notes.save("2330", "PRIVATE_NOTE_ONLY", 0)

    def data(self):
        return observations(self.wb, ["2330"], as_of="2026-09-29")

    def test_history_calendar_units_gaps_zero_and_identity(self):
        rows = parse_revenue_history(historical(), "2330", "now")
        self.assertEqual(rows[-1]["revenue"], "120")
        self.assertEqual(rows[-1]["mom"]["value"], "0.2")
        self.assertEqual(rows[-1]["yoy"]["value"], "0.2")
        self.assertIsNone(rows[1]["mom"]["value"])
        self.assertIsNone(rows[0]["published_at"])
        zero = historical(months=["202512", "202601", "202603"], values=[0, 1000, None])
        got = parse_revenue_history(zero, "2330", "now")
        self.assertIsNone(got[1]["mom"]["value"])
        self.assertIsNone(got[2]["revenue"])
        with self.assertRaises(ValueError):
            parse_revenue_history(historical(), "2317", "now")
        with self.assertRaises(ValueError):
            parse_revenue_history(
                historical(months=["202601", "202601"], values=[1, 2]), "2330", "now"
            )

    def test_source_conflict_revision_and_transaction(self):
        original = parse_revenue(revenue(), ["2330"], "old")[0]
        self.store.save("revenue", [original])
        other = parse_revenue_history(historical(), "2330", "new")[-1]
        with self.assertRaisesRegex(ValueError, "衝突"):
            self.store.save("revenue", [other])
        self.store.save("revenue_history", [other])
        self.store.save("revenue_history", [other])
        revised = copy.deepcopy(other)
        revised["revenue"] = "125"
        revised["raw"]["revenue_ntd"] = "125000"
        self.store.save("revenue_history", [revised])
        with self.assertRaises(ValueError):
            self.store.save("revenue_history", [other, other])
        self.assertEqual(self.store.read("revenue_history", "2330")[0]["revenue"], "125")
        self.assertEqual(self.store.read("revenue", "2330")[0]["revenue"], "120")
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("select count(*) from revisions").fetchone()[0], 1)
        from tw_stock_backtest.supplemental_views import panel_context

        view = panel_context(self.wb, "2330", "2026-09-29")
        self.assertEqual(view["source_conflicts"], ["2026-01"])
        self.assertIn("IIH", view["revenue_source"])

    def test_history_resume_per_stock_and_partial_coverage(self):
        client = Mock()
        client.get.side_effect = lambda kind, sid: historical(sid)
        result, path = update(
            self.settings,
            ["2330", "2317"],
            self.store.path,
            self.root / "updates",
            end=date(2026, 9, 29),
            kinds=("revenue",),
            revenue_source="history",
            client=client,
            revenue_start="2025-11",
            revenue_end="2026-01",
        )
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(result["exit_code"], 0)
        again, _ = update(
            self.settings,
            ["2330", "2317"],
            self.store.path,
            self.root / "updates",
            end=date(2026, 9, 29),
            kinds=("revenue",),
            revenue_source="history",
            client=client,
            revenue_start="2025-11",
            revenue_end="2026-01",
            resume=path,
        )
        self.assertEqual(client.get.call_count, 2)
        partial, _ = update(
            self.settings,
            ["2330"],
            self.store.path,
            self.root / "updates",
            end=date(2026, 9, 29),
            kinds=("revenue",),
            revenue_source="history",
            client=client,
            revenue_start="2025-01",
            revenue_end="2026-01",
        )
        self.assertEqual(partial["exit_code"], 2)
        self.assertIn("2025-02", partial["results"]["revenue:2330"]["missing_months"])
        self.assertEqual(self.store.update_states("2330")["revenue"]["counts"], {"partial": 1})
        # Source identity prevents replaying history as a latest-snapshot job.
        with self.assertRaises(ValueError):
            update(
                self.settings,
                ["2330", "2317"],
                self.store.path,
                self.root / "updates",
                end=date(2026, 9, 29),
                kinds=("revenue",),
                client=client,
                resume=path,
            )

    def test_overview_isolates_failure_and_does_not_invent_rank(self):
        with patch.object(Store, "read", side_effect=sqlite3.OperationalError):
            d = self.data()
        row = d["rows"][0]
        self.assertIsNotNone(row["close"])
        self.assertIsNone(row["rank"])
        self.assertIsNone(row["revenue"])
        self.assertTrue(row["warnings"])
        self.assertFalse(row["complete"])
        self.assertEqual(row["price_date"], "2026-09-29")
        self.assertNotIn("PRIVATE_NOTE_ONLY", json.dumps(d))

    def test_numeric_sort_missing_last_both_directions(self):
        rows = [
            dict(
                stock_id=s,
                name=None,
                close=v,
                status="eligible",
                complete=False,
                update_failed=False,
            )
            for s, v in [("2330", "9"), ("2317", "100"), ("2454", None)]
        ]
        self.assertEqual([r["close"] for r in filter_rows(rows, sort="close")], ["9", "100", None])
        self.assertEqual(
            [r["close"] for r in filter_rows(rows, sort="close", descending=True)],
            ["100", "9", None],
        )
        self.assertEqual(len(filter_rows(rows, status="incomplete")), 3)

    def test_comparisons_first_compatible_changed_strategy_missing_previous(self):
        d = self.data()
        self.assertIn("首次", compare(d, None)["message"])
        old = copy.deepcopy(d)
        old["run_id"] = "previous"
        old["rows"][0]["status"] = "filtered"
        d["rows"][0]["status"] = "eligible"
        self.assertIn("新通過", str(compare(d, old)["changes"]))
        old["rows"][0]["status"] = "unavailable"
        self.assertIn("不算新通過", str(compare(d, old)["changes"]))
        old["strategy_snapshot"]["sha256"] = "different"
        self.assertEqual(compare(d, old)["changes"], [])

    def test_revisions_and_updates_are_not_condition_changes(self):
        d = self.data()
        r = parse_revenue_history(historical(), "2330", "now")[-1]
        d["rows"][0]["revenue"] = r
        old = copy.deepcopy(d)
        old["run_id"] = "old"
        old["rows"][0]["revenue"]["raw"]["revenue_ntd"] = "999"
        change = compare(d, old)
        self.assertIn("修訂", str(change))
        self.assertNotIn("新通過", str(change))
        old["rows"][0]["revenue"]["source"] = "other"
        self.assertIn("不判定修訂", str(compare(d, old)))
        old["rows"][0]["revenue"] = None
        self.assertIn("資料更新", str(compare(d, old)))

    def test_snapshot_atomic_html_mail_csv_no_notes_or_environment(self):
        with patch.dict("os.environ", {"GMAIL_APP_PASSWORD": "DO_NOT_READ"}):
            d = self.data()
        root = self.wb.state / "watchlist-reports"
        path = save_snapshot(d, root)
        saved = load_digest(path / "summary.json")
        text, html = render_digest(saved)
        message = build_message(saved)
        self.assertIn(text, message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>名稱", html)
        self.assertEqual(
            next(message.iter_attachments()).get_payload(decode=True).decode(), saved["ranking_csv"]
        )
        preview_message(saved, self.root / "preview.eml")
        self.assertEqual((self.root / "preview.eml.preview/report.html").read_text(), html)
        for forbidden in ("PRIVATE_NOTE_ONLY", "DO_NOT_READ", "GMAIL_APP_PASSWORD"):
            self.assertNotIn(forbidden, json.dumps(saved, ensure_ascii=False))
        other = save_snapshot(d, root)
        self.assertNotEqual(path, other)
        self.assertEqual(len(previous_snapshots(root)), 2)
        with patch("tw_stock_backtest.watchlist_research.save_progress", side_effect=OSError):
            with self.assertRaises(OSError):
                save_snapshot(d, root)
        self.assertEqual(len(previous_snapshots(root)), 2)
        self.assertTrue(list(root.glob("*.incomplete")))

    def test_digest_numbers_are_formatted_for_display_only(self):
        import re

        from tw_stock_backtest.watchlist_research import digest_lines

        d = self.data()
        row = d["rows"][0]
        row.update(
            close=Decimal("92.9"),
            score="66.66666666666666666666666667",
            rank=1,
            research={
                "date": "2026-09-29",
                "pool": ["2330"],
                "population": ["2330"],
                "report_key": "k",
            },
        )
        row["revenue"] = {
            "period": "2026-09",
            "revenue": "112000",
            "mom": {"value": "0.009009009009009009009009009"},
            "yoy": {"value": None, "reason": "缺少對應期"},
            "source": "s",
            "basis": "b",
            "fetched_at": "2026-10-14T02:00:00+00:00",
        }
        row["diagnostic"] = {
            "conditions": [
                {"type": "rsi", "status": "passed", "value": "53.456789123", "minimum": "30"}
            ]
        }
        text = "\n".join(digest_lines(d))
        self.assertIn("收盤 92.90", text)
        self.assertIn("相對分數 66.67", text)
        self.assertIn("營收 2026-09：112,000 千元；月增 +0.90%；年增 —", text)
        self.assertIn("條件 RSI 位於上下限（含邊界）：通過（數值 53.46）", text)
        self.assertIn("抓取 2026-10-14 10:00", text)
        self.assertEqual(re.findall(r"\d+\.\d{5,}", text), [])
        # 保存的數值不變，只改顯示。
        self.assertEqual(row["score"], "66.66666666666666666666666667")

    def test_snapshot_page_has_a_single_top_heading(self):
        response = self.client.post(
            "/watchlist/snapshot", data={"csrf": self.token, "as_of": "2026-09-29"}
        )
        page = self.client.get(response.location).text
        self.assertEqual(page.count("<h1"), 1)
        self.assertIn("自選股研究總覽", page)
        # 郵件 HTML 保持原樣，仍以一級標題開頭。
        self.assertEqual(self.client.get(response.location + "/preview").text.count("<h1"), 1)

    def test_snapshot_html_routes_protection_and_json_job_coexist(self):
        self.assertEqual(self.client.post("/watchlist/snapshot").status_code, 403)
        response = self.client.post(
            "/watchlist/snapshot", data={"csrf": self.token, "as_of": "2026-09-29"}
        )
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        self.assertIn("自選股研究總覽", page.text)
        preview = self.client.get(response.location + "/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertNotIn("PRIVATE_NOTE_ONLY", preview.text)
        self.assertEqual(self.client.get("/watchlist?sort=close&direction=desc").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/watchlist/update", data={"csrf": self.token, "end": "2099-01-01"}
            ).status_code,
            400,
        )
        from tw_stock_backtest.cli.download_universe import save_progress

        jobid = "a" * 32
        folder = self.wb.state / jobid
        folder.mkdir()
        save_progress(
            {"run_id": jobid, "kind": "research", "status": "failed", "stocks": ["2330"]},
            folder / "job.json",
        )
        self.assertEqual(self.client.get("/jobs/" + jobid + "/job.json").status_code, 200)
        summary = self.client.get("/jobs/" + jobid + "/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertIn("不會自動重跑", summary.text)

    def test_background_update_kind_and_duplicate_suppression(self):
        with patch.object(self.wb.pool, "submit"):
            form = {"csrf": self.token, "update_kind": "revenue", "end": "2026-09-29"}
            one = self.client.post("/watchlist/update", data=form)
            two = self.client.post("/watchlist/update", data=form)
            self.assertEqual(one.status_code, 202)
            self.assertEqual(one.json["job_id"], two.json["job_id"])
            job = self.wb.jobs()[0]
            self.assertIn("history", job["arguments"])
            self.assertEqual(job["kind"], "watchlist_update")

    def test_price_failure_keeps_successful_supplemental_output(self):
        from tw_stock_backtest.cli.update_watchlist import run

        result = {
            "results": {
                "revenue:2330": {
                    "status": "success",
                    "rows": 13,
                    "stocks": {"2330": {"status": "success"}},
                }
            }
        }
        with (
            patch("tw_stock_backtest.cli.update_watchlist.update_stocks", side_effect=OSError),
            patch(
                "tw_stock_backtest.cli.update_watchlist.update",
                side_effect=lambda *a, **kw: (
                    result,
                    Path(a[3]) / "sub/supplemental_progress.json",
                ),
            ),
        ):
            got, path = run(
                self.settings, ["2330"], self.root / "orchestrated", end=date(2026, 9, 29)
            )
        self.assertEqual(got["exit_code"], 2)
        self.assertTrue(got["complete"])
        self.assertIn("prices:error", got["results"])

    def test_incomplete_snapshot_cannot_preview(self):
        root = self.root / "incomplete.incomplete"
        root.mkdir()
        d = self.data()
        d["report_complete"] = True
        (root / "summary.json").write_text(json.dumps(d))
        with self.assertRaises(ValueError):
            load_digest(root / "summary.json")

    def test_rank_changes_require_same_saved_population_and_earlier_date(self):
        d = self.data()
        row = d["rows"][0]
        row["rank"] = 1
        row["research"] = {
            "date": "2026-09-29",
            "pool": ["2330", "2317"],
            "population": ["2330", "2317"],
            "strategy_sha256": "hash",
        }
        old = copy.deepcopy(d)
        old["run_id"] = "old"
        old["rows"][0]["rank"] = 2
        self.assertNotIn("名次變化", str(compare(d, old)))
        old["rows"][0]["research"]["date"] = "2026-09-28"
        self.assertIn("名次變化 +1", str(compare(d, old)))
        old["rows"][0]["research"]["population"] = ["2330"]
        self.assertNotIn("名次變化", str(compare(d, old)))

    def test_historical_failure_keeps_prior_stock_and_other_stock_success(self):
        self.store.save("revenue_history", parse_revenue_history(historical(), "2330", "old"))

        def fetch(kind, sid):
            if sid == "2330":
                raise OSError("unavailable")
            return historical(sid)

        client = Mock()
        client.get.side_effect = fetch
        result, path = update(
            self.settings,
            ["2330", "2317"],
            self.store.path,
            self.root / "updates",
            end=date(2026, 9, 29),
            kinds=("revenue",),
            revenue_source="history",
            client=client,
        )
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(self.store.read("revenue_history", "2330")[-1]["fetched_at"], "old")
        self.assertEqual(self.store.update_states("2317")["revenue"]["counts"], {"success": 1})
        self.assertEqual(self.store.update_states("2330")["revenue"]["counts"], {"failed": 1})

    def test_price_update_passes_stock_metadata_to_existing_planner(self):
        from tw_stock_backtest.cli.fetch_stock import NoMarketData
        from tw_stock_backtest.cli.update_watchlist import run

        with patch(
            "tw_stock_backtest.research.fetch_month_with_retry",
            side_effect=NoMarketData("synthetic no data"),
        ) as fetch:
            result, path = run(
                self.settings, ["2330"], self.root / "prices", kind="prices", end=date(2026, 9, 29)
            )
        self.assertEqual(fetch.call_args.args[0], "2330")
        self.assertEqual(result["results"]["prices:2330"]["status"], "no_data")
        self.assertTrue(result["complete"])
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
