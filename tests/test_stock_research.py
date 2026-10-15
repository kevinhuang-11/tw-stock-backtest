import copy
import json
import re
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from test_strategy_library import records

from tw_stock_backtest.analysis.indicators import (
    exponential_moving_average,
    moving_average_convergence_divergence,
    relative_strength_index,
)
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.stock_research import (
    NoteConflict,
    Watchlist,
    chart_data,
    current_stock,
    historic_stock,
    stock_history,
)
from tw_stock_backtest.strategies import from_settings, snapshot
from tw_stock_backtest.workbench import Workbench, create_app


class StockResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(
            TEST_CONFIG.replace("AAA", "2330").replace("quantity = 10", "quantity = 1")
        )
        self.wb = Workbench(self.config, self.root / "reports", self.root / "state")
        self.addCleanup(self.wb.close)
        self.strategy = from_settings(self.wb.settings)
        self.wb.library.save(self.strategy)
        self.rows = records(count=90)
        save_records(self.rows, db_path=self.wb.settings["storage"]["database_path"])
        self.app = create_app(self.wb)
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = re.search(
            'name="csrf" value="([^"]+)"', self.client.get("/new/research").text
        )[1]

    def test_stock_query_cutoff_partial_and_empty(self):
        result = current_stock(self.wb, "2330", {"as_of": "2026-09-29", "display": "60"})
        self.assertEqual(result["date"], "2026-09-29")
        self.assertEqual(result["chart"]["rows"][-1]["date"], "2026-09-29")
        self.assertIsNone(result["ranking"])
        for url in (
            "/stocks?q=2330",
            "/stocks/2330?as_of=2026-09-29",
            "/stocks/9999",
            "/stocks/2330?as_of=2026-09-29&fragment=1",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
        self.assertIn("沒有可用行情", self.client.get("/stocks/9999").text)
        self.assertEqual(self.client.get("/stocks/bad").status_code, 400)
        self.assertEqual(self.client.get("/stocks/2330?display=999999").status_code, 400)
        self.assertEqual(self.client.get("/stocks/2330?report=../../config.toml").status_code, 404)
        self.assertEqual(self.client.get("/stocks/2330?as_of=2099-01-01").status_code, 400)

    def test_chart_values_match_core_and_display_does_not_change_seed(self):
        small = chart_data(self.rows, self.strategy, 60)
        large = chart_data(self.rows, self.strategy, 120)
        self.assertEqual(small["rows"], large["rows"][-60:])
        prices = [r["close"] for r in self.rows]
        last = small["rows"][-1]
        self.assertEqual(D(last["rsi"]), relative_strength_index(prices, 14)[-1])
        self.assertEqual(
            D(last["ema"]),
            exponential_moving_average(prices, self.strategy["factors"]["long_window"])[-1],
        )
        macd = moving_average_convergence_divergence(prices)[-1]
        for k, v in macd.items():
            self.assertEqual(D(last[k]), v)
        earlier = current_stock(self.wb, "2330", {"as_of": "2026-09-29"})["chart"]
        future = dict(self.rows[-1], date="2026-12-31", close=D(99999))
        save_records([future], db_path=self.wb.settings["storage"]["database_path"])
        self.assertEqual(earlier, current_stock(self.wb, "2330", {"as_of": "2026-09-29"})["chart"])

    def test_gaps_warmup_and_three_condition_states(self):
        rows = copy.deepcopy(self.rows)
        rows[3]["close"] = None
        chart = chart_data(rows, self.strategy, 120)
        self.assertIsNone(chart["rows"][3]["close"])
        self.assertIsNone(chart["rows"][3]["rsi"])
        # 缺價後從第 5 筆重新暖機：RSI 14 在第 19 筆才有值，之後與只用缺價後資料計算的結果相同。
        self.assertIsNone(chart["rows"][17]["rsi"])
        self.assertIsNotNone(chart["rows"][18]["rsi"])
        prices = [r["close"] for r in rows[4:]]
        self.assertEqual(D(chart["rows"][-1]["rsi"]), relative_strength_index(prices, 14)[-1])
        self.assertEqual(
            D(chart["rows"][-1]["ema"]),
            exponential_moving_average(prices, self.strategy["factors"]["long_window"])[-1],
        )
        self.assertIsNotNone(chart["rows"][-1]["sma"])
        self.assertTrue(any("第 4 筆" in r for r in chart["reasons"]))
        warm = chart_data(rows[:2], self.strategy, 60)
        self.assertIsNone(warm["rows"][-1]["ema"])
        self.assertIsNone(warm["rows"][-1]["signal"])
        for maximum, state in [("100", "eligible"), ("70", "filtered")]:
            s = copy.deepcopy(self.strategy)
            s["conditions"] = [{"type": "rsi", "period": 14, "minimum": "0", "maximum": maximum}]
            self.wb.library.save(s, replace=True)
            actual = current_stock(
                self.wb, "2330", {"as_of": "2026-09-29", "strategy_id": "baseline"}
            )
            self.assertEqual(actual["diagnostic"]["status"], state)
        # 早期缺價在重新暖機後不再影響；接近分析日的缺價仍讓 RSI 無法評估並說明日期。
        save_records([rows[3]], db_path=self.wb.settings["storage"]["database_path"])
        actual = current_stock(self.wb, "2330", {"as_of": "2026-09-29", "strategy_id": "baseline"})
        self.assertEqual(actual["diagnostic"]["status"], "filtered")
        recent = dict(rows[50], open=None, high=None, low=None, close=None)
        save_records([recent], db_path=self.wb.settings["storage"]["database_path"])
        actual = current_stock(self.wb, "2330", {"as_of": "2026-09-29", "strategy_id": "baseline"})
        self.assertEqual(actual["diagnostic"]["status"], "unavailable")
        self.assertIn(recent["date"], actual["diagnostic"]["conditions"][0]["reason"])
        self.assertEqual(
            self.client.get("/stocks/2330?strategy_id=baseline&as_of=2026-09-29").status_code, 200
        )

    def test_watchlist_persistence_conflict_escape_and_protection(self):
        url = "/watchlist/2330"
        note = "<script>alert(1)</script>"
        self.assertEqual(self.client.post(url, data={"note": note}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(
            url,
            data={"csrf": self.token, "note": note, "revision": 0},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        saved = Watchlist(self.wb.state / "private/watchlist.sqlite3").get("2330")
        self.assertEqual(saved["note"], note)
        self.assertEqual(saved["revision"], 1)
        page = self.client.get("/watchlist").text
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert", page)
        self.assertEqual(
            self.client.post(
                url, data={"csrf": self.token, "note": "stale", "revision": 0}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                url, data={"csrf": self.token, "note": "update", "revision": 1}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                url, data={"csrf": self.token, "action": "remove", "revision": 2}
            ).status_code,
            302,
        )
        self.assertEqual(Watchlist(self.wb.state / "private/watchlist.sqlite3").items(), [])
        # Tombstone prevents a stale original page from resurrecting removed notes.
        self.assertEqual(
            self.client.post(
                url, data={"csrf": self.token, "note": "stale", "revision": 0}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/stocks/2330", headers={"Host": "evil.example"}).status_code, 400
        )
        with self.client.get("/static/stock.js") as response:
            self.assertEqual(response.status_code, 200)
        self.assertIn(
            "default-src 'self'", self.client.get("/stocks/2330").headers["Content-Security-Policy"]
        )

    def bundle(self, day="2026-09-29", rank=1):
        return {
            "summary": {
                "analysis_date": day,
                "finished_at": day + "T10:00:00Z",
                "run_id": "a",
                "status": "success",
                "strategy_snapshot": snapshot(self.strategy),
                "ranking_population": ["2330", "2317"],
                "evaluations": [
                    {
                        "stock_id": "2330",
                        "status": "eligible",
                        "signal": {"close": "123", "date": day},
                        "actual": {"latest": day},
                        "conditions": [],
                    }
                ],
            },
            "mode": "factors",
            "settings": self.wb.settings,
            "universe": {
                "stocks": [{"stock_id": "2330", "name": "歷史名稱"}, {"stock_id": "2317"}]
            },
            "rankings": [{"stock_id": "2330", "rank": rank, "score": "50"}],
        }

    def test_history_rerun_comparability_missing_and_snapshot(self):
        before = self.bundle("2026-09-28", 2)
        today = self.bundle()
        rerun = copy.deepcopy(today)
        rerun["summary"]["run_id"] = "z"
        rerun["rankings"][0]["rank"] = 2
        rows = stock_history({"a": before, "b": today, "c": rerun}, "2330")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["report_key"], "c")
        self.assertEqual(rows[0]["change"], 0)
        altered = copy.deepcopy(rerun)
        altered["summary"]["ranking_population"] = ["2330"]
        rows = stock_history({"a": before, "c": altered}, "2330")
        self.assertIsNone(rows[0]["change"])
        self.assertTrue(rows[0]["limitations"])
        missing = copy.deepcopy(today)
        missing["rankings"] = []
        missing["summary"]["evaluations"] = []
        self.assertEqual(stock_history({"b": missing}, "2330")[0]["status"], "not_saved")
        missing["summary"]["evaluations"] = [{"stock_id": "2330", "status": "filtered"}]
        self.assertEqual(stock_history({"b": missing}, "2330")[0]["status"], "filtered")
        frozen = historic_stock("2330", "a", before)
        self.assertEqual(frozen["metadata"]["name"], "歷史名稱")
        self.assertEqual(frozen["chart"]["rows"], [])
        self.wb.library.save({**self.strategy, "name": "修改後策略"}, replace=True)
        self.assertEqual(frozen["strategy_snapshot"], before["summary"]["strategy_snapshot"])

    def test_stock_backtest_confirmation_to_effective_report(self):
        form = {
            "csrf": self.token,
            "strategy_id": "baseline",
            "stocks": "2330",
            "start": "2026-09-16",
            "end": "2026-09-29",
            "cash": "50000",
            "quantity": "2",
            "max_positions": "1",
            "sizing": "fixed_quantity",
            "slippage": "0.002",
        }
        response = self.client.post("/stocks/2330/backtest", data=form)
        self.assertEqual(response.status_code, 200)
        self.assertIn("單檔條件", response.text)
        self.assertEqual(self.wb.jobs(), [])
        form.update(confirm="yes", strategy_hash=snapshot(self.strategy)["sha256"])
        response = self.client.post("/stocks/2330/backtest", data=form)
        self.assertEqual(response.status_code, 302)
        self.wb.pool.submit(lambda: None).result(timeout=15)
        job = self.wb.jobs()[0]
        self.assertEqual(job["status"], "success")
        settings_path = next((self.wb.state / job["run_id"] / "reports").glob("*/settings.json"))
        saved = json.loads(settings_path.read_text())
        self.assertEqual(D(saved["settings"]["backtest"]["initial_cash"]), D(50000))
        self.assertEqual(D(saved["settings"]["backtest"]["slippage_rate"]), D("0.002"))
        self.assertEqual(saved["settings"]["backtest"]["quantity"], 2)
        self.assertEqual(saved["settings"]["backtest"]["max_positions"], 1)
        self.assertEqual(saved["settings"]["backtest"]["sizing_mode"], "fixed_quantity")
        self.assertEqual(
            D(saved["settings"]["costs"]["commission_rate"]),
            self.wb.settings["costs"]["commission_rate"],
        )
        self.assertEqual(saved["strategy_snapshot"], snapshot(self.strategy))
        self.assertIn("--quantity", job["arguments"])
        self.assertIn("2", job["arguments"])
        form["strategy_hash"] = "stale"
        self.assertEqual(self.client.post("/stocks/2330/backtest", data=form).status_code, 409)
        form["strategy_hash"] = snapshot(self.strategy)["sha256"]
        form["end"] = "2026-12-31"
        self.assertEqual(self.client.post("/stocks/2330/backtest", data=form).status_code, 400)

    def test_queued_and_running_job_cancellation(self):
        import subprocess
        from threading import Event
        from unittest.mock import Mock

        from tw_stock_backtest.cli.download_universe import save_progress

        entered, release = Event(), Event()

        def running(*args, **kwargs):
            entered.set()
            release.wait(5)
            return Mock(returncode=0)

        self.wb.runner = running
        first = self.wb.submit({"kind": "research"})
        self.assertTrue(entered.wait(2))
        second = self.wb.submit({"kind": "research"})
        self.assertEqual(self.client.post("/jobs/" + second + "/cancel").status_code, 403)
        self.assertEqual(
            self.client.post("/jobs/" + second + "/cancel", data={"csrf": self.token}).status_code,
            302,
        )
        release.set()
        self.wb.pool.submit(lambda: None).result(timeout=10)
        self.assertEqual(
            json.loads((self.wb.state / second / "job.json").read_text())["status"], "cancelled"
        )
        # Real execution wrapper terminates its controlled process, never arbitrary PIDs.
        self.wb.runner = subprocess.run
        job_id = "f" * 32
        directory = self.wb.state / job_id
        directory.mkdir()
        job = {"run_id": job_id, "kind": "research", "status": "queued"}
        save_progress(job, directory / "job.json")
        entered.clear()
        stopped = Event()
        process = Mock()

        def wait(timeout):
            entered.set()
            self.assertTrue(stopped.wait(5))
            return -15

        process.wait.side_effect = wait
        process.terminate.side_effect = stopped.set
        with patch("tw_stock_backtest.workbench.subprocess.Popen", return_value=process):
            self.wb.pending += 1
            future = self.wb.pool.submit(self.wb.execute, job, directory, [])
            self.assertTrue(entered.wait(2))
            self.wb.cancel(job_id)
            future.result(timeout=10)
        self.assertEqual(json.loads((directory / "job.json").read_text())["status"], "cancelled")
        process.terminate.assert_called_once()

    def test_saved_report_http_never_reads_current_prices(self):
        self.wb.submit({"kind": "research", "strategy_id": "baseline", "as_of": "2026-09-29"})
        self.wb.pool.submit(lambda: None).result(timeout=10)
        catalog = self.wb.catalog()
        key = next(
            k
            for k, v in catalog.items()
            if v["details"].get("kind") == "research" and v["details"].get("status") == "success"
        )
        with patch(
            "tw_stock_backtest.stock_research.load_records",
            side_effect=AssertionError("must not recalculate"),
        ):
            response = self.client.get("/stocks/2330?report=" + key)
            self.assertEqual(response.status_code, 200)
            self.assertIn("原研究報表快照", response.text)
            self.assertIn("未保存完整 K 線快照", response.text)
        self.assertEqual(
            self.client.get("/stocks/2330?report=" + key + "&as_of=2026-09-29").status_code, 400
        )

    def test_queued_settings_do_not_drift_after_confirmation(self):
        from threading import Event
        from unittest.mock import Mock

        entered, release = Event(), Event()

        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            return Mock(returncode=1)

        self.wb.runner = blocked
        job_id = self.wb.submit({"kind": "research"})
        self.assertTrue(entered.wait(2))
        self.config.write_text(
            self.config.read_text().replace(
                'commission_rate = "0.001425"', 'commission_rate = "0.005"'
            )
        )
        try:
            job = json.loads((self.wb.state / job_id / "job.json").read_text())
            frozen = load_config(job["arguments"][job["arguments"].index("--config") + 1])
            self.assertEqual(frozen["costs"], self.wb.settings["costs"])
            self.assertEqual(frozen["storage"], self.wb.settings["storage"])
        finally:
            release.set()
            self.wb.pool.submit(lambda: None).result(timeout=10)
