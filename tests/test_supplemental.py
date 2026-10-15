import copy
import json
import re
import sqlite3
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.cli.update_supplemental import main, update
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.supplemental import (
    Client,
    Store,
    cumulative,
    growth,
    institution_plan,
    number,
    parse_institution,
    parse_revenue,
    roc_month,
)
from tw_stock_backtest.workbench import Workbench, create_app


def revenue(month="11501", current="120", previous="100", year="80"):
    return [
        {
            "公司代號": "2330",
            "公司名稱": "測試",
            "資料年月": month,
            "出表日期": "1150210",
            "營業收入-當月營收": current,
            "營業收入-上月營收": previous,
            "營業收入-去年當月營收": year,
            "營業收入-上月比較增減(%)": "20",
            "營業收入-去年同月增減(%)": "50",
        }
    ]


def institution(day="2026-09-29"):
    fields = ["證券代號", "證券名稱"]
    values = ["2330", "測試"]
    for prefix, suffix, nums in [
        ("外陸資", "(不含外資自營商)", ("100", "150", "-50")),
        ("外資自營商", "", ("99", "0", "99")),
        ("投信", "", ("20", "5", "15")),
        ("自營商", "(自行買賣)", ("10", "15", "-5")),
        ("自營商", "(避險)", ("8", "3", "5")),
    ]:
        fields.extend(prefix + x + "股數" + suffix for x in ("買進", "賣出", "買賣超"))
        values.extend(nums)
    fields.extend(["自營商買賣超股數", "三大法人買賣超股數"])
    values.extend(["0", "-35"])
    return {
        "stat": "OK",
        "date": day.replace("-", ""),
        "hints": "單位：股",
        "fields": fields,
        "data": [values],
    }


class SupplementalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(TEST_CONFIG.replace("AAA", "2330"))
        self.settings = load_config(self.config)
        self.store = Store(self.root / "supplemental.sqlite3")
        from tw_stock_backtest.data.universe import save_universe

        save_universe(
            [
                {
                    "stock_id": sid,
                    "name": "測試",
                    "market": "上市",
                    "industry": "測試",
                    "listed_date": "2000-01-01",
                    "cfi_code": "ESVUFR",
                }
                for sid in ("2330", "2317")
            ],
            Path(self.settings["storage"]["database_path"]).with_name("listed_stocks.json"),
        )

    def test_numbers_month_and_growth(self):
        self.assertEqual(roc_month("11412"), "2025-12")
        self.assertEqual(roc_month("115/01"), "2026-01")
        for value in ("NaN", "Infinity", "bad"):
            with self.assertRaises(ValueError):
                number(value)
        self.assertEqual(number("-1,234", True), "-1234")
        self.assertIsNone(number("--"))
        self.assertEqual(growth("120", "100")["value"], "0.2")
        for base in (None, "0", "-10"):
            self.assertIsNone(growth("120", base)["value"])
        row = parse_revenue(revenue(), ["2330"], "t")[0]
        self.assertEqual(row["mom"]["value"], "0.2")
        self.assertEqual(row["yoy"]["value"], "0.5")
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["unit"], "TWD thousand")
        # Referenced prior month is Dec, not any last saved row (e.g. Oct).
        self.store.save("revenue", parse_revenue(revenue("11410", current="999"), ["2330"], "t"))
        self.store.save("revenue", [row])
        self.assertEqual(len(self.store.read("revenue", "2330")), 2)
        self.assertEqual(self.store.read("revenue", "2330")[-1]["previous_month"], "100")

    def test_institution_categories_negative_and_validation(self):
        row = parse_institution(institution(), "2026-09-29", ["2330"], "t")[0]
        self.assertEqual(row["total"], "-35")
        self.assertEqual(row["groups"]["foreign_dealer"]["net"], "99")
        self.assertEqual(Decimal(row["groups"]["foreign"]["net"]) / 1000, Decimal("-.05"))
        bad = institution()
        bad["data"][0][-1] = "64"
        with self.assertRaises(ValueError):
            parse_institution(bad, "2026-09-29", ["2330"], "t")
        with self.assertRaises(ValueError):
            parse_institution(institution(), "2026-09-28", ["2330"], "t")
        self.assertEqual(parse_institution({"stat": "查無資料"}, "2026-09-29", ["2330"], "t"), [])

    def test_atomic_revision_and_rollback(self):
        row = parse_revenue(revenue(), ["2330"], "t")[0]
        self.store.save("revenue", [row])
        self.store.save("revenue", [row])
        changed = parse_revenue(revenue(current="130"), ["2330"], "u")[0]
        self.store.save("revenue", [changed])
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("select count(*) from revisions").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.store.save("revenue", [row, row])
        self.assertEqual(self.store.read("revenue", "2330")[0]["revenue"], "130")

    def test_window_missing_dates_and_future(self):
        days = [str(date(2026, 9, 1) + timedelta(days=i)) for i in range(21)]
        rows = [parse_institution(institution(d), d, ["2330"], "t")[0] for d in days]
        self.assertEqual(cumulative(rows, days, days[19], 20)["totals"]["foreign"], "-1000")
        self.assertTrue(cumulative(rows, days, days[19], 20)["complete"])
        gap = rows[:10] + rows[11:]
        result = cumulative(gap, days, days[19], 20)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_dates"], [days[10]])
        self.assertIsNone(result["totals"]["foreign"])
        self.assertFalse(cumulative(rows, days[:3], days[2], 5)["complete"])

    def test_incremental_cross_year_and_explicit_backfill(self):
        self.assertEqual(len(institution_plan(self.store, ["2330"], date(2026, 1, 5))), 35)
        row = parse_institution(institution("2025-12-30"), "2025-12-30", ["2330"], "t")[0]
        self.store.save("institution", [row])
        self.assertEqual(institution_plan(self.store, ["2330"], date(2026, 1, 5))[0], "2025-12-24")
        self.assertEqual(
            institution_plan(self.store, ["2330"], date(2025, 12, 1), date(2025, 12, 1)),
            ["2025-12-01"],
        )

    def test_update_independent_failures_and_resume_identity(self):
        client = Mock()
        client.get.side_effect = lambda kind, day: (
            revenue() if kind == "revenue" else (_ for _ in ()).throw(ValueError("bad"))
        )
        kwargs = dict(end=date(2026, 9, 29), start=date(2026, 9, 29), client=client)
        result, path = update(self.settings, ["2330"], self.store.path, self.root / "out", **kwargs)
        self.assertEqual(result["exit_code"], 2)
        self.assertTrue(self.store.read("revenue", "2330"))
        client.get.side_effect = lambda kind, day: institution(day)
        retried, _ = update(
            self.settings, ["2330"], self.store.path, self.root / "out", resume=path, **kwargs
        )
        self.assertEqual(retried["exit_code"], 0)
        with self.assertRaises(ValueError):
            update(
                self.settings, ["2317"], self.store.path, self.root / "out", resume=path, **kwargs
            )
        self.assertEqual(len(self.store.read("institution", "2330")), 1)

    def test_latest_only_revenue_and_unknown_missing_not_unpublished(self):
        client = Mock()
        client.get.return_value = revenue()
        result, _ = update(
            self.settings,
            ["2330", "2317"],
            self.store.path,
            self.root / "out",
            end=date(2026, 9, 29),
            kinds=("revenue",),
            client=client,
        )
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(result["results"]["revenue:latest"]["stocks"]["2317"]["status"], "no_data")
        result, _ = update(
            self.settings,
            ["2330"],
            self.store.path,
            self.root / "out",
            end=date(2026, 9, 29),
            kinds=("revenue",),
            revenue_start="2024-01",
            revenue_end="2024-12",
            client=client,
        )
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(len(self.store.read("revenue", "2330")), 1)

    def test_http_no_download_security_history_and_job_dedup(self):
        wb = Workbench(self.config, self.root / "reports", self.root / "jobs")
        self.addCleanup(wb.close)
        app = create_app(wb)
        app.testing = True
        client = app.test_client()
        response = client.get("/stocks/2330/supplemental?end=2026-09-29")
        self.assertEqual(response.status_code, 200)
        self.assertIn("公告時間未提供", response.text)
        self.assertEqual(wb.jobs(), [])
        self.assertEqual(
            client.post("/supplemental/update", data={"stock_id": "2330"}).status_code, 403
        )
        self.assertEqual(client.get("/supplemental/update").status_code, 405)
        token = re.search('name="csrf" value="([^"]+)"', response.text)[1]
        self.assertEqual(
            client.post(
                "/supplemental/update", data={"csrf": token, "scope": "watchlist"}
            ).status_code,
            400,
        )
        self.assertEqual(client.get("/stocks/2330/supplemental?end=2099-01-01").status_code, 400)
        with patch.object(wb.pool, "submit"):
            a = wb.submit({"kind": "supplemental", "stocks": "2330", "end": "2026-09-29"})
            b = wb.submit({"kind": "supplemental", "stocks": "2330", "end": "2026-09-29"})
            self.assertEqual(a, b)
            self.assertIn("--end", wb.jobs()[0]["arguments"])

    def test_cli_selection_and_end_to_end_offline(self):
        with patch(
            "tw_stock_backtest.data.supplemental.Client.get",
            side_effect=lambda kind, day: revenue() if kind == "revenue" else institution(day),
        ):
            code = main(
                [
                    "--config",
                    str(self.config),
                    "--database",
                    str(self.store.path),
                    "--stocks",
                    "2330",
                    "--start",
                    "2026-09-29",
                    "--end",
                    "2026-09-29",
                    "--output-dir",
                    str(self.root / "out"),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.store.read("institution", "2330")), 1)

    def test_background_result_catalog_and_nonempty_display(self):
        import subprocess

        def runner(command, **kwargs):
            self.assertEqual(command[2], "tw_stock_backtest.cli.update_supplemental")
            with patch(
                "tw_stock_backtest.data.supplemental.Client.get",
                side_effect=lambda kind, day: revenue() if kind == "revenue" else institution(day),
            ):
                code = main(command[3:])
            return subprocess.CompletedProcess(command, code)

        wb = Workbench(self.config, self.root / "reports", self.root / "jobs", runner=runner)
        self.addCleanup(wb.close)
        wb.submit(
            {"kind": "supplemental", "stocks": "2330", "start": "2026-09-29", "end": "2026-09-29"}
        )
        wb.pool.shutdown(wait=True)
        self.assertEqual(wb.jobs()[0]["status"], "success")
        catalog = wb.catalog()
        self.assertEqual(len(catalog), 1)
        client = create_app(wb).test_client()
        self.assertEqual(client.get("/reports/" + next(iter(catalog))).status_code, 200)
        page = client.get("/stocks/2330/supplemental?end=2026-09-29")
        self.assertEqual(page.status_code, 200)
        self.assertIn("2026-01", page.text)
        self.assertIn("-35", page.text)
        self.assertIn("不能證明當時已知", page.text)
        self.assertNotIn("2026-09-29", client.get("/stocks/2330/supplemental?end=2026-09-28").text)
        self.assertIn("supplemental.js", page.text)

    def test_official_client_retry_policy_and_scope(self):
        from urllib.error import HTTPError

        from tw_stock_backtest.data.supplemental import listed_scope

        with self.assertRaises(ValueError):
            listed_scope(self.settings, ["9999"])
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"[]"
        with patch(
            "tw_stock_backtest.data.supplemental.urlopen",
            side_effect=[HTTPError("official", 503, "temporary", {}, None), response],
        ) as request:
            client = Client(
                dict(
                    timeout_seconds=10,
                    request_interval_seconds=3,
                    max_attempts=2,
                    retry_wait_seconds=1,
                ),
                sleep=Mock(),
            )
            self.assertEqual(client.get("revenue"), [])
            self.assertEqual(request.call_count, 2)
        with patch(
            "tw_stock_backtest.data.supplemental.urlopen",
            side_effect=HTTPError("official", 403, "blocked", {}, None),
        ) as request:
            with self.assertRaises(HTTPError):
                client.get("revenue")
            self.assertEqual(request.call_count, 1)

    def test_embedded_panel_failure_isolation_and_historical_separation(self):
        wb = Workbench(self.config, self.root / "reports", self.root / "jobs")
        self.addCleanup(wb.close)
        client = create_app(wb).test_client()
        database = Path(wb.settings["storage"]["database_path"]).with_name("supplemental.sqlite3")
        Store(database).save(
            "institution", parse_institution(institution(), "2026-09-29", ["2330"], "t")
        )
        page = client.get("/stocks/2330?as_of=2026-01-01")
        self.assertEqual(page.status_code, 200)
        original, supp = page.text.split('id="supplemental-research"', 1)
        self.assertNotIn("-35", original)
        self.assertIn("-35", supp)
        self.assertIn("不代表歷史當時已知資訊", supp)
        self.assertIn("價格、成交量與指標", original)
        with patch(
            "tw_stock_backtest.supplemental_views.Store.read",
            side_effect=sqlite3.DatabaseError("private path secret"),
        ):
            page = client.get("/stocks/2330")
        self.assertEqual(page.status_code, 200)
        self.assertIn("價格、成交量與指標", page.text)
        self.assertIn("其他研究區塊仍可使用", page.text)
        self.assertNotIn("private path secret", page.text)

    def test_month_selector_fragment_and_update_parameters(self):
        wb = Workbench(self.config, self.root / "reports", self.root / "jobs")
        self.addCleanup(wb.close)
        client = create_app(wb).test_client()
        database = Path(wb.settings["storage"]["database_path"]).with_name("supplemental.sqlite3")
        Store(database).save("revenue", parse_revenue(revenue(), ["2330"], "t"))
        page = client.get(
            "/stocks/2330/supplemental?fragment=1&end=2026-09-29&month_start=2026-02&month_end=2026-03"
        )
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("<html", page.text)
        self.assertIn("尚未取得此範圍的月營收", page.text)
        self.assertEqual(
            client.get(
                "/stocks/2330/supplemental?month_start=2026-13&month_end=2026-14"
            ).status_code,
            400,
        )
        token = re.search('name="csrf" value="([^"]+)"', page.text)[1]
        with patch.object(wb.pool, "submit"):
            for kind in ("revenue", "institution"):
                response = client.post(
                    "/supplemental/update",
                    data={
                        "csrf": token,
                        "stock_id": "2330",
                        "supplemental_kind": kind,
                        "end": "2026-09-29",
                        "revenue_start": "2026-01",
                        "revenue_end": "2026-01",
                    },
                )
                self.assertEqual(response.status_code, 303)
            self.assertEqual(len(wb.jobs()), 2)
            arguments = wb.jobs()[0]["arguments"]
            self.assertIn("--kind", arguments)
            self.assertEqual(arguments[arguments.index("--revenue-start") + 1], "2026-01")
            self.assertEqual(
                client.post(
                    "/supplemental/update",
                    data={"csrf": token, "stock_id": "2330", "supplemental_kind": "unknown"},
                ).status_code,
                400,
            )
            self.assertEqual(len(wb.jobs()), 2)

    def test_independent_update_states_survive_other_kind_and_failure(self):
        client = Mock()
        client.get.side_effect = lambda kind, day: (
            revenue() if kind == "revenue" else institution(day)
        )
        kwargs = dict(end=date(2026, 9, 29), start=date(2026, 9, 29), client=client)
        update(self.settings, ["2330"], self.store.path, self.root / "out", **kwargs)
        institution_state = self.store.update_states("2330")["institution"]
        client.get.side_effect = ConnectionError("secret must not be saved")
        result, _ = update(
            self.settings,
            ["2330"],
            self.store.path,
            self.root / "out",
            kinds=("revenue",),
            **kwargs,
        )
        self.assertEqual(result["exit_code"], 1)
        states = self.store.update_states("2330")
        self.assertEqual(states["institution"], institution_state)
        self.assertEqual(states["revenue"]["counts"], {"failed": 1})
        self.assertNotIn("secret", json.dumps(states))
        self.assertTrue(self.store.read("revenue", "2330"))

    def test_summary_query_never_downloads_or_requires_config(self):
        import io
        from contextlib import redirect_stdout

        path = self.root / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "identity": {"schema": 1},
                    "complete": False,
                    "results": {
                        "revenue:latest": {
                            "status": "failed",
                            "reason": "来源無法取得",
                            "stocks": {"2330": {"status": "failed"}},
                        }
                    },
                }
            )
        )
        with (
            patch("tw_stock_backtest.data.supplemental.Client.get") as fetch,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = main(["--config", str(self.root / "missing.toml"), "--show-summary", str(path)])
        self.assertEqual(code, 0)
        fetch.assert_not_called()
        self.assertIn("incomplete", out.getvalue())
        self.assertIn("2330", out.getvalue())
