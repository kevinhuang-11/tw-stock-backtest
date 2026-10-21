"""合成示例名單：名稱可讀取，所有連網入口在發出請求前被拒絕。"""

import contextlib
import io
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.cli import (
    download_universe,
    fetch_corporate_actions,
    fetch_stock,
    run_research,
    update_supplemental,
    update_universe,
    update_watchlist,
)
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.supplemental import listed_scope
from tw_stock_backtest.data.universe import (
    OFFLINE_REASON,
    NetworkDisabled,
    load_universe,
    save_synthetic_universe,
    save_universe,
    universe_path_for,
)
from tw_stock_backtest.research import update_stocks
from tw_stock_backtest.stock_research import Watchlist, identities
from tw_stock_backtest.workbench import Workbench, create_app

SYNTHETIC_STOCKS = [
    {
        "stock_id": "9001",
        "name": "示範股 A（合成）",
        "industry": "示範產業甲",
        "market": "合成示例",
    },
    {
        "stock_id": "9002",
        "name": "示範股 B（合成）",
        "industry": "示範產業乙",
        "market": "合成示例",
    },
]


class OfflineDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(TEST_CONFIG.replace("AAA", "9001"), encoding="utf-8")
        self.settings = load_config(self.config)
        self.listed = universe_path_for(self.settings["storage"]["database_path"])
        save_synthetic_universe(
            SYNTHETIC_STOCKS,
            self.listed,
            source_url="synthetic://offline-example",
        )

        # 任何一個被呼叫都代表有網路請求漏出去。
        self.network = Mock(side_effect=AssertionError("不應發出網路請求"))
        for target in (
            "tw_stock_backtest.cli.fetch_stock.urlopen",
            "tw_stock_backtest.cli.fetch_corporate_actions.urlopen",
            "tw_stock_backtest.data.supplemental.urlopen",
            "tw_stock_backtest.data.universe.urlopen",
        ):
            patcher = patch(target, self.network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def workbench(self):
        self.runner = Mock()
        wb = Workbench(
            self.config,
            self.root / "reports",
            self.root / "jobs",
            runner=self.runner,
        )
        self.addCleanup(wb.close)
        Watchlist(wb.state / "private" / "watchlist.sqlite3").save("9001", "", 0)
        client = create_app(wb).test_client()
        page = client.get("/watchlist")
        token = re.search('name="csrf" value="([^"]+)"', page.text)[1]
        return wb, client, token

    def run_cli(self, main, argv, *, uses_sys_argv=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            if uses_sys_argv:
                with patch("sys.argv", ["prog", *argv]):
                    code = main()
            else:
                code = main(argv)
        return code, output.getvalue()

    def test_synthetic_universe_is_readable_and_honestly_labelled(self):
        snapshot = load_universe(self.listed)

        self.assertEqual(snapshot["scope"], "synthetic_example")
        self.assertEqual(snapshot["source_url"], "synthetic://offline-example")
        text = self.listed.read_text(encoding="utf-8")
        for forbidden in ("twse.com.tw", "上市", "cfi_code", "listed_date"):
            self.assertNotIn(forbidden, text)

        wb, _, _ = self.workbench()
        self.assertEqual(
            identities(wb)["9001"],
            {"stock_id": "9001", "name": "示範股 A（合成）", "industry": "示範產業甲"},
        )

    def test_shared_update_paths_refuse_before_any_request(self):
        with self.assertRaises(NetworkDisabled):
            listed_scope(self.settings, ["9001"])

        with self.assertRaises(NetworkDisabled):
            update_stocks(
                [{"stock_id": "9001"}],
                self.settings,
                self.settings_end(),
            )

        with self.assertRaises(NetworkDisabled):
            update_watchlist.run(
                self.settings,
                ["9001"],
                self.root / "updates",
                kind="prices",
            )

        self.network.assert_not_called()
        self.assertFalse((self.root / "updates").exists())

    def settings_end(self):
        from datetime import date

        return date(2026, 9, 29)

    def test_every_network_cli_refuses_with_exit_code_one(self):
        config = ["--config", str(self.config)]
        cases = [
            (
                update_supplemental.main,
                [*config, "--stocks", "9001", "--output-dir", str(self.root / "out")],
                False,
            ),
            (
                update_watchlist.main,
                [*config, "--stocks", "9001", "--output-dir", str(self.root / "out")],
                False,
            ),
            (
                run_research.main,
                [
                    *config,
                    "--stocks",
                    "9001",
                    "--as-of",
                    "2026-09-29",
                    "--output-dir",
                    str(self.root / "out"),
                ],
                False,
            ),
            (
                fetch_stock.main,
                [*config, "--stock", "9001", "--start", "2026-09-01", "--end", "2026-09-29"],
                True,
            ),
            (
                download_universe.main,
                [
                    *config,
                    "--universe",
                    str(self.listed),
                    "--start",
                    "2026-09-01",
                    "--end",
                    "2026-09-29",
                    "--log-dir",
                    str(self.root / "logs"),
                ],
                True,
            ),
            (update_universe.main, ["--output", str(self.listed)], True),
            (
                fetch_corporate_actions.main,
                [*config, "--stocks", "9001", "--start", "2026-01-01", "--end", "2026-09-29"],
                False,
            ),
        ]
        original = self.listed.read_bytes()

        for main, argv, uses_sys_argv in cases:
            with self.subTest(cli=main.__module__):
                code, output = self.run_cli(main, argv, uses_sys_argv=uses_sys_argv)
                self.assertEqual(code, 1)
                self.assertIn("合成示例", output)

        self.network.assert_not_called()
        self.assertEqual(self.listed.read_bytes(), original)
        self.assertFalse((self.root / "out").exists())
        self.assertFalse((self.root / "logs").exists())

    def test_workbench_rejects_updates_on_the_server(self):
        wb, client, token = self.workbench()
        requests = [
            ("/watchlist/update", {"update_kind": "all"}),
            ("/watchlist/update", {"update_kind": "prices"}),
            ("/supplemental/update", {"stock_id": "9001"}),
            ("/supplemental/update", {"scope": "watchlist"}),
            ("/jobs", {"kind": "research", "stocks": "9001", "download": "yes"}),
        ]

        for url, data in requests:
            with self.subTest(url=url, data=data):
                response = client.post(url, data={"csrf": token, **data})
                self.assertEqual(response.status_code, 400)
                self.assertIn(OFFLINE_REASON, response.get_json()["errors"]["_form"])

        wb.pool.shutdown(wait=True)
        self.assertEqual(wb.jobs(), [])
        self.runner.assert_not_called()
        self.network.assert_not_called()

    def test_buttons_are_disabled_with_reason(self):
        _, client, _ = self.workbench()
        pages = {
            "/watchlist": "更新自選股資料（最多 10 檔）</button>",
            "/stocks/9001": "建立更新工作</button>",
        }

        for url, label in pages.items():
            with self.subTest(url=url):
                text = client.get(url).text
                self.assertIn("<button disabled>" + label, text)
                self.assertIn(OFFLINE_REASON, text)

        # 首頁不再有研究表單；下載選項只在建立研究頁，並同樣停用。首頁說明目前是離線示範。
        home = client.get("/").text
        self.assertNotIn('name="download"', home)
        self.assertIn("目前是離線示範", home)

        # 個股頁的單檔更新入口在離線示範中不可點；即使帶入參數也不會勾選下載。
        stock = client.get("/stocks/9001").text
        self.assertIn('<span class="btn btn-sm" aria-disabled="true"', stock)
        self.assertNotIn("download=yes", stock)
        self.assertIn(
            'name="download" value="yes" disabled>',
            client.get("/new/research?stocks=9001&download=yes").text,
        )

        research = client.get("/new/research").text
        self.assertIn('name="download" value="yes" disabled', research)
        self.assertIn(OFFLINE_REASON, research)

    def test_offline_research_job_is_still_accepted(self):
        wb, client, token = self.workbench()

        response = client.post(
            "/jobs",
            data={"csrf": token, "kind": "research", "stocks": "9001"},
        )

        self.assertEqual(response.status_code, 302)
        wb.pool.shutdown(wait=True)
        self.assertEqual(len(wb.jobs()), 1)
        self.assertIn("--skip-download", wb.jobs()[0]["arguments"])
        self.network.assert_not_called()

    def test_official_universe_keeps_buttons_enabled(self):
        save_universe(
            [
                {
                    "stock_id": "9001",
                    "name": "測試",
                    "market": "上市",
                    "industry": "測試",
                    "listed_date": "2000-01-01",
                    "cfi_code": "ESVUFR",
                }
            ],
            self.listed,
        )
        _, client, _ = self.workbench()

        text = client.get("/watchlist").text

        self.assertIn("<button>更新自選股資料（最多 10 檔）</button>", text)
        self.assertNotIn(OFFLINE_REASON, text)
        self.assertEqual(
            [s["stock_id"] for s in listed_scope(self.settings, ["9001"])["stocks"]],
            ["9001"],
        )


if __name__ == "__main__":
    unittest.main()
