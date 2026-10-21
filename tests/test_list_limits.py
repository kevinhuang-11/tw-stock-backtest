"""只列出前幾筆的清單都要說明總數或有路徑看到全部（股票搜尋以外的地方）。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.data.universe import save_synthetic_universe
from tw_stock_backtest.stock_research import identities
from tw_stock_backtest.workbench import Workbench, create_app


class ListLimitTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = self.root / "config.toml"
        config.write_text(TEST_CONFIG, encoding="utf-8")
        self.workbench = Workbench(config, self.root / "reports", self.root / "state")
        self.addCleanup(self.workbench.close)
        self.client = create_app(self.workbench).test_client()

    def test_identities_has_no_hidden_cap(self):
        # 原本名單只讀前 3000 筆、行情庫只讀 3000 檔，超過的股票默默消失。
        stocks = [
            {
                "stock_id": f"{1000 + n}",
                "name": f"合成{n}",
                "market": "合成示例",
                "industry": "其他業",
            }
            for n in range(3200)
        ]
        save_synthetic_universe(
            stocks, self.root / "data" / "listed_stocks.json", source_url="synthetic://test"
        )
        found = identities(self.workbench)
        self.assertIn("4199", found)
        self.assertEqual(len([s for s in found if s.isdigit()]), 3200)

    def test_home_says_how_many_jobs_exist(self):
        for n in range(8):
            folder = self.workbench.state / f"job-{n}"
            folder.mkdir(parents=True)
            (folder / "job.json").write_text(
                json.dumps(
                    {
                        "run_id": f"job-{n}",
                        "kind": "research",
                        "status": "success",
                        "queued_at": f"2026-10-0{n + 1}T00:00:00+00:00",
                        "stocks": ["2330"],
                    }
                ),
                encoding="utf-8",
            )
        text = self.client.get("/").text
        self.assertEqual(text.count('<li id="job-'), 6)
        self.assertIn("共 8 筆工作，這裡只列出最近 6 筆", text)

    def test_watchlist_says_how_many_snapshots_exist(self):
        root = self.workbench.state / "watchlist-reports"
        for n in range(12):
            folder = root / f"2026-10-{n + 1:02d}_snap"
            folder.mkdir(parents=True)
            (folder / "summary.json").write_text(
                json.dumps(
                    {
                        "report_complete": True,
                        "digest_kind": "watchlist",
                        "generated_at": f"2026-10-{n + 1:02d}T00:00:00+00:00",
                        "run_id": f"r{n}",
                        "strategy_snapshot": {"definition": {"name": "示例"}},
                        "counts": {"requested": 1},
                    }
                ),
                encoding="utf-8",
            )
        (root / "unfinished.incomplete").mkdir()
        (root / "unfinished.incomplete" / "summary.json").write_text("{}", encoding="utf-8")
        text = self.client.get("/watchlist").text
        self.assertIn("共 12 份快照，這裡只列出最近 10 份", text)


if __name__ == "__main__":
    unittest.main()
