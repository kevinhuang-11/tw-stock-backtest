"""資料新鮮度：以本機行情庫推算落後的行情日，並顯示在總覽、個股與自選頁。"""
import re
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data.supplemental import Store
from tw_stock_backtest.data_status import data_status, market_lag, price_summary
from tw_stock_backtest.stock_research import Watchlist
from tw_stock_backtest.workbench import Workbench, create_app

DAYS = ("2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14")


def rows(sid, days):
    return [{"stock_id": sid, "date": day, "open": Decimal("10"), "high": Decimal("11"),
             "low": Decimal("9"), "close": Decimal("10"), "volume": 1000, "turnover": 10000,
             "trade_count": 10} for day in days]


class StatusCase(unittest.TestCase):
    """共用的資料：2330 到 10/14，1101 停在 10/12。"""
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = self.root / "config.toml"
        config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
        self.workbench = Workbench(config, self.root / "reports", self.root / "jobs")
        self.addCleanup(self.workbench.close)
        self.db = Path(self.workbench.settings["storage"]["database_path"])
        save_records(rows("2330", DAYS), db_path=self.db)
        save_records(rows("1101", DAYS[:2]), db_path=self.db)
        self.client = create_app(self.workbench).test_client()


class DataStatusTests(StatusCase):
    def test_lag_counts_later_local_trading_days(self):
        dates, latest = price_summary(self.db)
        self.assertEqual(dates, DAYS)
        self.assertEqual(latest, {"2330": "2026-10-14", "1101": "2026-10-12"})
        self.assertEqual(market_lag("2026-10-14", dates), 0)
        self.assertEqual(market_lag("2026-10-12", dates), 2)
        # 週末等不在行情庫的日期，同樣以之後出現的行情日計算。
        self.assertEqual(market_lag("2026-10-10", dates), 3)
        self.assertIsNone(market_lag(None, dates))
        self.assertIsNone(market_lag("2026-10-12", ()))

    def test_summary_includes_listing_and_supplemental(self):
        Store(self.db.with_name("supplemental.sqlite3")).save("institution", [
            {"stock_id": "2330", "period": "2026-10-13", "raw": {}, "source": "s", "unit": "shares",
             "fetched_at": "t", "groups": {}}])
        status = data_status(self.db, today=date(2026, 10, 15))
        self.assertEqual(status["price_latest"], "2026-10-14")
        self.assertEqual(status["calendar_days"], 1)
        self.assertEqual((status["stocks"], status["behind"], status["behind_oldest"]), (2, 1, "2026-10-12"))
        self.assertIsNone(status["listing"])
        self.assertEqual(status["supplemental"], {"institution": {"latest": "2026-10-13", "stocks": 1}})

    def test_summary_updates_after_new_prices(self):
        self.assertEqual(data_status(self.db)["behind"], 1)
        save_records(rows("1101", DAYS[2:]), db_path=self.db)
        self.assertEqual(data_status(self.db)["behind"], 0)

    def test_empty_environment(self):
        status = data_status(self.root / "missing.db")
        self.assertEqual((status["price_latest"], status["stocks"], status["behind"]), (None, 0, 0))

    def test_pages_show_how_fresh_the_data_is(self):
        home = self.client.get("/").text
        self.assertIn('id="data-status"', home)
        self.assertIn("1 / 2 檔，最早停在 2026-10-12", home)
        self.assertIn("--universe data/listed_stocks.json --allow-partial", home)

        self.assertIn("資料截至 2026-10-12 · 落後 2 個行情日", self.client.get("/stocks/1101").text)
        current = self.client.get("/stocks/2330").text
        self.assertIn("資料截至 2026-10-14", current)
        self.assertNotIn("落後", current.split('class="cluster freshness-line"')[1][:300])

        Watchlist(self.workbench.state / "private" / "watchlist.sqlite3").save("1101", "", 0)
        watch = self.client.get("/watchlist").text
        self.assertIn('<span class="badge badge-warn">落後 2 個行情日</span>', watch)

        for url in ("/new/research", "/new/backtest"):
            with self.subTest(url=url):
                text = self.client.get(url).text
                self.assertIn("本機行情最新到 2026-10-14", text)
                self.assertIn("有 1 檔停在較舊日期（最早 2026-10-12）", text)


class SingleStockUpdateTests(StatusCase):
    def test_stock_page_links_to_a_prefilled_research_with_download(self):
        text = self.client.get("/stocks/1101").text
        self.assertIn('href="/new/research?stocks=1101&download=yes"', text)
        form = self.client.get("/new/research?stocks=1101&download=yes").text
        self.assertIn('<input name="stocks" value="1101" required', form)
        self.assertIn('<input type="checkbox" name="download" value="yes" checked>', form)
        self.assertIn("更新後研究", form)
        plain = self.client.get("/new/research").text
        self.assertIn('<input type="checkbox" name="download" value="yes">', plain)

    def test_submitting_the_prefilled_form_requests_a_download(self):
        from unittest.mock import Mock
        self.workbench.runner = Mock(return_value=Mock(returncode=0))
        token = re.search('name="csrf" value="([^"]+)"', self.client.get("/new/research").text)[1]
        response = self.client.post("/jobs", data={"csrf": token, "kind": "research",
                                                   "stocks": "1101", "download": "yes"})
        self.assertEqual(response.status_code, 302)
        self.workbench.pool.shutdown(wait=True)
        arguments = self.workbench.jobs()[0]["arguments"]
        self.assertNotIn("--skip-download", arguments)
        self.assertEqual(arguments[arguments.index("--stocks") + 1], "1101")


if __name__ == "__main__":
    unittest.main()
