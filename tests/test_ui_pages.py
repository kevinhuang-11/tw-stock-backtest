"""新版頁面：能開啟、內容正確、導覽不連到不存在的頁面。"""
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.web_views import created_at, describe_report, report_list
from tw_stock_backtest.workbench import Workbench, create_app

SNAPSHOT = {"definition": {"id": "baseline", "name": "基準示例"}, "sha256": "a" * 64}


def write(folder, name, document):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


class PageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = self.root / "config.toml"
        config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
        self.reports = self.root / "reports"
        self.workbench = Workbench(config, self.reports, self.root / "jobs")
        self.addCleanup(self.workbench.close)
        self.client = create_app(self.workbench).test_client()

    def add_research(self, name="20260929T010000_000000Z_aaaaaaaa", day="2026-09-29"):
        folder = self.reports / name
        write(folder, "summary.json", {
            "report_complete": True, "status": "success", "analysis_date": day,
            "strategy_snapshot": SNAPSHOT, "ranking_mode": "factors",
            "counts": {"requested": 2, "evaluated": 2, "excluded": 0, "ranked": 2},
            "ranking_population": ["2330", "2317"], "evaluations": [], "excluded": [],
        })
        write(folder, "rankings.json", [
            {"rank": 1, "stock_id": "2330", "date": day, "score": "66.66666666666666666666666667",
             "factors": {}, "factor_scores": {}},
            {"rank": 2, "stock_id": "2317", "date": day, "score": "33.3", "factors": {}, "factor_scores": {}},
        ])
        write(folder, "universe.json", {"source": "stocks", "stocks": [
            {"stock_id": "2330", "name": "測試甲", "industry": "測試"},
            {"stock_id": "2317", "name": "測試乙", "industry": "測試"}]})
        write(folder, "settings.json", {"settings": {}})
        return folder

    def add_backtest(self, name="20260930T020000_000000Z_bbbbbbbb", total="0.1076"):
        folder = self.reports / name
        performance = {"total_return": total, "max_drawdown": "0.0928",
                       "invested_days": 18, "average_exposure": "0.76"}
        write(folder, "settings.json", {"requested_start": "2026-09-01", "requested_end": "2026-09-29",
                                        "strategy_snapshot": SNAPSHOT, "settings": {}})
        write(folder, "results.json", {"strategy": {"performance": performance, "result": {}},
                                       "benchmark": {"performance": {**performance, "total_return": "0.05"}}})
        write(folder, "input_data.json", {})
        (folder / "trades.csv").write_text("", encoding="utf-8")
        (folder / "equity.csv").write_text("", encoding="utf-8")
        return folder

    def nav_links(self, text):
        nav = text[text.index('<nav class="site-nav"'):text.index("</nav>")]
        return re.findall(r'<a href="([^"]+)"', nav)

    def assert_navigation_works(self, text):
        links = self.nav_links(text)
        self.assertTrue(links)
        for href in links:
            with self.subTest(href=href):
                self.assertNotEqual(self.client.get(href).status_code, 404)


class ReportListTests(PageCase):
    def test_empty_state_explains_what_to_do(self):
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200)
        self.assertIn("還沒有任何報表", page.text)
        self.assertIn('aria-current="page"', page.text)
        self.assert_navigation_works(page.text)

    def test_reports_have_readable_titles_and_formatted_values(self):
        self.add_research()
        self.add_backtest()
        text = self.client.get("/reports").text

        self.assertIn("研究排名 · 基準示例", text)
        self.assertIn("分析日 2026-09-29 · 2 檔排名", text)
        self.assertIn("回測 · 基準示例", text)
        self.assertIn("2026-09-01 ～ 2026-09-29", text)
        # 報酬率以百分比與漲跌色顯示；內部狀態代碼轉成中文。
        self.assertIn('<span class="up">+10.76%</span>', text)
        self.assertIn('<span class="badge badge-ok">完成</span>', text)
        self.assertNotIn(">success<", text)
        self.assertNotIn(">complete<", text)
        # 執行編號不再當成報表名稱，只留在提示文字。
        self.assertNotIn(">20260929T010000_000000Z_aaaaaaaa<", text)
        # 最新的排在前面；建立時間換算成台北時間。
        self.assertLess(text.index("回測 · 基準示例"), text.index("研究排名 · 基準示例"))
        self.assertIn("2026-09-30 10:00", text)

    def test_kind_filter_and_invalid_kind(self):
        self.add_research()
        self.add_backtest()
        text = self.client.get("/reports?kind=backtest").text
        self.assertIn("回測 · 基準示例", text)
        self.assertNotIn("研究排名 · 基準示例", text)
        self.assertIn("沒有這個類型的報表", self.client.get("/reports?kind=watchlist").text)
        self.assertEqual(self.client.get("/reports?kind=<script>").status_code, 400)

    def test_ranking_comparison_needs_two_finished_research_reports(self):
        self.add_research()
        self.assertNotIn('action="/compare"', self.client.get("/reports").text)
        self.add_research("20260922T010000_000000Z_cccccccc", "2026-09-22")
        text = self.client.get("/reports").text
        self.assertIn('action="/compare"', text)
        keys = re.findall(r'<option value="([0-9a-f]{24})"', text)
        self.assertEqual(len(set(keys)), 2)
        self.assertEqual(self.client.get(f"/compare?left={keys[0]}&right={keys[1]}").status_code, 200)

    def test_falling_return_uses_green_and_missing_metric_shows_dash(self):
        self.add_backtest(total="-0.0321")
        self.add_research()
        text = self.client.get("/reports").text
        self.assertIn('<span class="down">−3.21%</span>', text)
        self.assertIn('<span class="subtle">—</span>', text)


class NewResearchPageTests(PageCase):
    def test_form_posts_the_same_fields_to_the_same_endpoint(self):
        page = self.client.get("/new/research")
        self.assertEqual(page.status_code, 200)
        text = page.text
        form = text[text.index('<form class="card" method="post" action="/jobs">'):text.index("</form>")]
        self.assertIn('name="kind" value="research"', form)
        for name in ("csrf", "strategy_id", "stocks", "as_of", "partial", "download"):
            with self.subTest(field=name):
                self.assertIn(f'name="{name}"', form)
        # 沒有 JavaScript 時的退路：一般文字欄位，預設帶入設定檔的股票池。
        self.assertRegex(form, r'<input name="stocks" value="2330" required')
        self.assert_navigation_works(text)

    def test_submitting_the_form_creates_a_research_job(self):
        from unittest.mock import Mock
        self.workbench.runner = Mock(return_value=Mock(returncode=0))
        text = self.client.get("/new/research").text
        token = re.search('name="csrf" value="([^"]+)"', text)[1]
        response = self.client.post("/jobs", data={"csrf": token, "kind": "research", "stocks": "2330"})
        self.assertEqual(response.status_code, 302)
        self.workbench.pool.shutdown(wait=True)
        jobs = self.workbench.jobs()
        self.assertEqual([job["kind"] for job in jobs], ["research"])
        self.assertIn("--skip-download", jobs[0]["arguments"])

    def test_stocks_and_strategy_can_be_prefilled_from_the_query(self):
        text = self.client.get("/new/research?stocks=2317+2454").text
        self.assertIn('<input name="stocks" value="2317 2454"', text)
        for bad in ("abc", "2330 2330", "1 2 3", '"><script>'):
            with self.subTest(stocks=bad):
                fallback = self.client.get("/new/research", query_string={"stocks": bad}).text
                self.assertIn('<input name="stocks" value="2330"', fallback)
                self.assertNotIn("<script>alert", fallback)

    def test_download_option_is_enabled_with_an_official_universe(self):
        text = self.client.get("/new/research").text
        self.assertIn('<input type="checkbox" name="download" value="yes">', text)
        self.assertNotIn("連線更新已停用", text)


class ReportDescriptionTests(unittest.TestCase):
    def describe(self, details, name="20260929T010000_000000Z_aaaaaaaa"):
        with TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.mkdir()
            return describe_report("k", {"path": path, "details": details})

    def test_each_kind_gets_a_title(self):
        cases = {
            "策略比較 · 2 份策略": {"kind": "comparison", "status": "complete",
                              "strategies": [{"id": "a"}, {"id": "b"}],
                              "common": {"start": "2026-09-01", "end": "2026-09-29"}},
            "跨期間比較 · 2 份策略": {"kind": "period_comparison", "status": "partial",
                               "strategies": [{"id": "a"}, {"id": "b"}, {"id": "a"}],
                               "periods": [{}, {}, {}]},
            "自選快照 · 基準示例": {"kind": "watchlist", "status": "complete",
                              "strategy_snapshot": SNAPSHOT, "counts": {"requested": 3, "eligible": 2}},
            "營收與法人更新": {"kind": "supplemental", "status": "success",
                          "identity": {"stocks": ["2330"]}},
            "未知類型": {"kind": "unknown", "status": "unreadable"},
            "研究排名": {"kind": "research", "status": "incomplete"},
        }
        for title, details in cases.items():
            with self.subTest(title=title):
                self.assertEqual(self.describe(details)["title"], title)
        self.assertEqual(self.describe(cases["跨期間比較 · 2 份策略"])["subtitle"], "3 個期間")
        self.assertEqual(self.describe(cases["自選快照 · 基準示例"])["subtitle"], "3 檔 · 通過 2 檔")

    def test_created_time_comes_from_the_folder_name(self):
        with TemporaryDirectory() as directory:
            for name, expected in (
                ("20260929T010203_000004Z_aaaaaaaa", "2026-09-29T01:02:03.000004+00:00"),
                ("2026-10-14T024138.910778_0000_8de28a58", "2026-10-14T02:41:38.910778+00:00"),
            ):
                path = Path(directory) / name
                path.mkdir()
                self.assertEqual(created_at(path).isoformat(), expected)
            other = Path(directory) / "custom-name"
            other.mkdir()
            self.assertIsNotNone(created_at(other).tzinfo)

    def test_list_is_newest_first_and_filterable(self):
        with TemporaryDirectory() as directory:
            catalog = {}
            for key, name, kind in (("a", "20260901T000000_000000Z_x", "research"),
                                    ("b", "20260903T000000_000000Z_y", "backtest"),
                                    ("c", "20260902T000000_000000Z_z", "research")):
                path = Path(directory) / name
                path.mkdir()
                catalog[key] = {"path": path, "details": {"kind": kind, "status": "incomplete"}}
            self.assertEqual([r["key"] for r in report_list(catalog)], ["b", "c", "a"])
            self.assertEqual([r["key"] for r in report_list(catalog, "research")], ["c", "a"])


if __name__ == "__main__":
    unittest.main()
