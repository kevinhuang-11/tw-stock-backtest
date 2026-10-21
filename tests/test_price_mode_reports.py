"""報表頁顯示價格口徑；不同口徑的報表與快照不互相比較。"""

import json
import unittest

from test_ui_pages import PageCase, write

from tw_stock_backtest.research_digest import differences
from tw_stock_backtest.watchlist_research import compare
from tw_stock_backtest.workbench import compare_rankings


def research_detail(mode=None):
    detail = {
        "kind": "research",
        "status": "success",
        "rankings": [{"stock_id": "2330", "rank": 1}],
    }
    if mode:
        detail["price_adjustment"] = {"mode": mode, "actions": [], "uncovered": {}}
    return detail


class ComparisonGuardTests(unittest.TestCase):
    def test_research_rankings_with_different_modes_are_not_compared(self):
        self.assertEqual(
            len(compare_rankings(research_detail("adjusted"), research_detail("adjusted"))), 1
        )
        # 舊報表沒有紀錄，視為 raw。
        self.assertEqual(len(compare_rankings(research_detail(), research_detail("raw"))), 1)
        with self.assertRaisesRegex(ValueError, "價格口徑不同"):
            compare_rankings(research_detail("adjusted"), research_detail())

    def test_digest_differences_report_the_mode(self):
        def bundle(mode):
            summary = {"ranking_population": ["2330"]}
            if mode:
                summary["price_adjustment"] = {"mode": mode}
            return {"summary": summary, "settings": {}, "mode": "factors", "universe": {}}

        self.assertIn(
            "價格口徑不同（還原價／原始價格），不比較名次",
            differences(bundle("adjusted"), bundle(None)),
        )
        self.assertNotIn(
            "價格口徑不同（還原價／原始價格），不比較名次", differences(bundle("raw"), bundle(None))
        )

    def test_watchlist_snapshots_with_different_modes_are_not_comparable(self):
        def snapshot(mode, status):
            value = {
                "run_id": "r",
                "specified_pool": ["2330"],
                "strategy_snapshot": {"sha256": "a"},
                "requested_date": None,
                "rows": [{"stock_id": "2330", "status": status}],
            }
            if mode:
                value["price_adjustment"] = {"mode": mode}
            return value

        same = compare(snapshot("adjusted", "eligible"), snapshot("adjusted", "filtered"))
        self.assertEqual(same["changes"][0]["messages"], ["條件改變：新通過"])
        mixed = compare(snapshot("adjusted", "eligible"), snapshot(None, "filtered"))
        self.assertIn("價格口徑不同", mixed["message"])
        self.assertEqual(mixed["changes"], [])


class ReportPageTests(PageCase):
    def key(self):
        return next(iter(self.workbench.catalog()))

    def test_backtest_page_shows_the_mode_notes_and_corporate_actions(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        results["strategy"]["result"] = {
            "price_adjustment": {"mode": "adjusted", "actions": [], "uncovered": {}},
            "corporate_action_events": [
                {
                    "applied_on": "2026-09-29",
                    "stock_id": "1441",
                    "kind": "capital_reduction",
                    "shares_before": 1000,
                    "shares_after": 861,
                    "cash": "0",
                    "needs_review": False,
                }
            ],
        }
        results["benchmark"]["result"] = {"corporate_action_events": []}
        write(folder, "results.json", results)
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn('<span class="badge badge-info">還原價（adjusted）</span>', text)
        self.assertIn("現金股利與減資退還股款於生效日入帳（簡化，實際發放日通常較晚）", text)
        self.assertIn("未扣股利所得稅與二代健保補充保費", text)
        self.assertIn("1,000 → 861", text)

    def test_old_backtest_reports_are_labelled_raw(self):
        self.add_backtest()
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn('<span class="badge badge-neutral">原始價格（raw）</span>', text)
        self.assertIn("報酬不含股利", text)
        self.assertNotIn("<h2>公司行動</h2>", text)

    def test_research_page_shows_the_mode(self):
        self.add_research()
        text = self.client.get(f"/reports/{self.key()}").text
        self.assertIn("價格口徑：", text)
        self.assertIn("原始價格（raw）", text)


if __name__ == "__main__":
    unittest.main()
