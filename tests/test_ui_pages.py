"""新版頁面：能開啟、內容正確、導覽不連到不存在的頁面。"""
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.strategies import from_settings
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


class NewBacktestPageTests(PageCase):
    def add_strategies(self, *ids):
        for sid in ids:
            self.workbench.library.save({**from_settings(self.workbench.settings), "id": sid, "name": sid})

    def forms(self, text):
        return {re.search(r'name="kind" value="([a-z_]+)"', form)[1]: form
                for form in re.findall(r'<form class="card" method="post" action="/jobs">.*?</form>', text, re.S)}

    def test_three_forms_post_the_same_fields_as_before(self):
        self.add_strategies("first", "second")
        page = self.client.get("/new/backtest")
        self.assertEqual(page.status_code, 200)
        forms = self.forms(page.text)
        self.assertEqual(set(forms), {"backtest", "comparison", "period_comparison"})
        expected = {
            "backtest": ("csrf", "strategy_id", "ranking", "stocks", "start", "end", "cash",
                         "quantity", "max_positions", "sizing", "slippage_percent"),
            "comparison": ("csrf", "strategies", "stocks", "start", "end"),
            "period_comparison": ("csrf", "strategies", "stocks", "periods"),
        }
        for kind, names in expected.items():
            for name in names:
                with self.subTest(kind=kind, field=name):
                    self.assertIn(f'name="{name}"', forms[kind])
        # 執行參數預設帶入設定檔的值。
        self.assertIn('name="cash" min="0" step="any" value="1000" required', forms["backtest"])
        self.assertIn('name="quantity" min="1" step="1" value="10" required', forms["backtest"])
        self.assert_navigation_works(page.text)

    def test_strategies_are_checkboxes_not_a_multi_select(self):
        self.add_strategies("second", "first")
        forms = self.forms(self.client.get("/new/backtest").text)
        for kind in ("comparison", "period_comparison"):
            with self.subTest(kind=kind):
                self.assertNotIn("<select", forms[kind].split('name="strategies"')[0][-200:])
                self.assertNotIn("multiple", forms[kind])
                self.assertEqual(
                    re.findall(r'<input type="checkbox" name="strategies" value="([a-z_]+)">', forms[kind]),
                    ["first", "second"])

    def test_checkbox_selection_is_accepted_by_the_unchanged_backend(self):
        from unittest.mock import Mock
        self.add_strategies("first", "second")
        self.workbench.runner = Mock(return_value=Mock(returncode=0))
        token = re.search('name="csrf" value="([^"]+)"', self.client.get("/new/backtest").text)[1]
        base = {"csrf": token, "kind": "comparison", "stocks": "2330",
                "start": "2026-09-01", "end": "2026-09-29"}
        # 勾選框以多個同名欄位送出，與原本的多選清單相同。
        self.assertEqual(self.client.post("/jobs", data={**base, "strategies": ["first", "second"]}).status_code, 302)
        self.assertEqual(self.client.post("/jobs", data={**base, "strategies": ["first"]}).status_code, 400)
        self.workbench.pool.shutdown(wait=True)
        self.assertEqual([job["kind"] for job in self.workbench.jobs()], ["comparison"])

    def test_fewer_than_two_strategies_explains_instead_of_offering_checkboxes(self):
        text = self.client.get("/new/backtest").text
        self.assertIn("策略庫至少要有兩份策略才能比較", text)


class HomePageTests(PageCase):
    def test_first_visit_shows_where_to_start(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("從這裡開始", page.text)
        for href in ("/watchlist", "/new/research", "/reports"):
            self.assertIn(f'href="{href}"', page.text)
        self.assertIn("還沒有工作", page.text)
        self.assertIn("還沒有任何報表", page.text)
        # 表單都移到各自的頁面，首頁不再是四張長表單。
        self.assertNotIn('action="/jobs"', page.text)
        self.assert_navigation_works(page.text)

    def test_recent_reports_and_jobs_replace_the_getting_started_steps(self):
        from unittest.mock import Mock
        self.add_research()
        self.add_backtest()
        self.workbench.runner = Mock(return_value=Mock(returncode=0))
        token = re.search('name="csrf" value="([^"]+)"', self.client.get("/new/research").text)[1]
        self.client.post("/jobs", data={"csrf": token, "kind": "research", "stocks": "2330"})
        self.workbench.pool.shutdown(wait=True)

        text = self.client.get("/").text
        self.assertNotIn("從這裡開始", text)
        self.assertIn("研究排名 · 基準示例", text)
        self.assertIn('<span class="num up">+10.76%</span>', text)
        self.assertIn("最近研究的分析日", text)
        self.assertIn(">2026-09-29<", text)
        # 工作以中文類型與狀態標籤顯示，保留摘要、紀錄連結。
        self.assertIn("<strong>研究排名</strong>", text)
        job = self.workbench.jobs()[0]
        self.assertIn(f'href="/jobs/{job["run_id"]}/summary"', text)
        self.assertIn(f'href="/jobs/{job["run_id"]}/output.log"', text)

    def test_running_jobs_can_still_be_cancelled_from_the_home_page(self):
        directory = self.workbench.state / ("a" * 32)
        directory.mkdir(parents=True)
        (directory / "job.json").write_text(json.dumps({
            "run_id": "a" * 32, "kind": "backtest", "status": "running", "stocks": ["2330"],
            "notification": "pending", "started_at": "2026-10-14T02:00:00+00:00"}), encoding="utf-8")
        text = self.client.get("/").text
        self.assertIn(f'action="/jobs/{"a" * 32}/cancel"', text)
        self.assertIn('name="csrf"', text)
        self.assertIn('<span class="badge badge-info">執行中</span>', text)


class ResearchReportPageTests(PageCase):
    def key(self):
        return next(iter(self.workbench.catalog()))

    def test_candidates_are_a_readable_table(self):
        self.add_research()
        key = self.key()
        text = self.client.get(f"/reports/{key}").text
        self.assertIn("候選股總覽", text)
        self.assertIn(f'<a class="cell-main" href="/stocks/2330?report={key}">2330 測試甲</a>', text)
        self.assertIn('<td class="num">66.67</td>', text)
        self.assertNotIn("66.66666666666666666666666667", text.split("原始資料")[0])
        for href in (f"/reports/{key}/preview?top=10", f"/reports/{key}/csv",
                     "/new/research?stocks=2317+2330&strategy_id=baseline"):
            self.assertIn(f'href="{href}"', text)
        self.assertIn('aria-current="page"', text.split("</nav>")[0].split("報表</a>")[0][-200:])
        self.assert_navigation_works(text)

    def test_top_search_and_sort_still_work(self):
        self.add_research()
        key = self.key()
        self.assertEqual(self.client.get(f"/reports/{key}?top=1").text.count('class="cell-main"'), 1)
        self.assertEqual(self.client.get(f"/reports/{key}?top=99").status_code, 400)
        full = self.client.get(f"/reports/{key}?q=2317").text.split('id="rankings"')[1]
        self.assertIn('href="/stocks/2317', full)
        self.assertNotIn('href="/stocks/2330', full)
        ordered = self.client.get(f"/reports/{key}?sort=stock_id").text.split('id="rankings"')[1]
        self.assertLess(ordered.index("2317"), ordered.index("2330"))

    def test_failed_research_explains_instead_of_an_empty_table(self):
        folder = self.add_research()
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        summary.update(status="failed")
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (folder / "rankings.json").write_text("[]", encoding="utf-8")
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn("候選股總覽", text)
        self.assertIn("本次沒有可呈現的候選股", text)


class BacktestReportPageTests(PageCase):
    def key(self):
        return next(iter(self.workbench.catalog()))

    def test_backtest_shows_key_figures_trades_and_settings(self):
        folder = self.add_backtest()
        (folder / "trades.csv").write_text(
            "source,stock_id,signal_date,date,action,price,quantity,amount,commission,tax,cash_change,cash_after\n"
            "strategy,2330,2026-09-01,2026-09-02,BUY,100.5,1000,100500,143,0,-100643,899357\n"
            "strategy,2330,2026-09-10,2026-09-11,SELL,110,1000,110000,156,330,109514,1008871\n"
            "benchmark,2330,,2026-09-01,BUY,99,1000,99000,141,0,-99141,900859\n", encoding="utf-8-sig")
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn("回測 · 基準示例", text)
        self.assertIn('<div class="stat-value num">+10.76%</div>', text)
        self.assertIn("基準 +5.00%", text)
        self.assertIn('<div class="stat-value num">629</div>', text)  # 143 + 156 + 330，只算策略
        self.assertIn("策略共 2 筆成交", text)
        self.assertIn('<td class="num">100.50</td>', text)
        self.assertIn('<span class="badge badge-info">買進</span>', text)
        self.assertIn("訊號 2026-09-10", text)
        self.assertNotIn("total_return</td>", text)
        self.assert_navigation_works(text)

    def test_backtest_without_trades_has_an_empty_state(self):
        self.add_backtest(total="-0.0321")
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn("這段期間沒有成交", text)
        self.assertIn('<span class="num down">−3.21%</span>', text)

    def test_comparison_lists_strategies_and_benchmark(self):
        write(self.reports / "20260930T030000_000000Z_cmp", "comparison.json", {
            "kind": "comparison", "report_complete": True, "note": "不選出最佳策略。",
            "common": {"stocks": ["2330"], "start": "2026-09-01", "end": "2026-09-29"},
            "strategies": [{"id": "a", "name": "甲策略", "total_return": "0.0115", "max_drawdown": "0.0003",
                            "fees_and_taxes": "40", "trades": 2, "average_exposure": "0.0213",
                            "no_candidate_days": 0, "unavailable_days": 32}],
            "benchmark": {"total_return": "0.05", "max_drawdown": "0.01", "average_exposure": "0.9"}})
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn("策略比較 · 1 份策略", text)
        self.assertIn('<span class="num up">+1.15%</span>', text)
        self.assertIn("買進持有基準", text)
        self.assertIn("0 / 32", text)
        self.assertIn("不選出最佳策略。", text)
        self.assertIn(f'href="/reports/{self.key()}/csv"', text)


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
