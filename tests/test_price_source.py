"""價格模式設定、公司行動涵蓋範圍與 PriceSource；全部使用臨時資料庫與合成資料。"""

import tomllib
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.config import load_config, validate_settings
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.prices import ADJUSTED, RAW, PriceSource, price_mode

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"]


def price_row(day, close):
    return {
        "stock_id": "1441",
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "turnover": 1,
        "trade_count": 1,
    }


REDUCTION = {
    "stock_id": "1441",
    "effective_date": DAYS[3],
    "kind": ca.CAPITAL_REDUCTION,
    "previous_close": D("8.72"),
    "reference_price": D("10.13"),
    "cash_dividend": D(0),
    "stock_dividend_per_1000": D(0),
    "shares_per_1000": D("861"),
    "cash_return": D(0),
    "rights_per_1000": D(0),
    "rights_price": D(0),
    "halt_start": None,
    "needs_review": False,
    "review_notes": [],
    "sources": ["https://www.twse.com.tw/example"],
    "raw": {},
}


class ConfigTests(unittest.TestCase):
    def test_mode_defaults_to_adjusted_and_rejects_unknown_values(self):
        base = tomllib.loads(TEST_CONFIG)
        self.assertEqual(
            validate_settings(base)["corporate_actions"], {"mode": "adjusted", "rights": "refuse"}
        )
        self.assertEqual(
            validate_settings({**base, "corporate_actions": {"mode": "raw"}})["corporate_actions"][
                "mode"
            ],
            "raw",
        )
        self.assertEqual(
            validate_settings({**base, "corporate_actions": {"rights": "decline"}})[
                "corporate_actions"
            ],
            {"mode": "adjusted", "rights": "decline"},
        )
        for bad in (
            {"mode": "split"},
            {"mode": "raw", "extra": 1},
            "raw",
            {"rights": "subscribe"},
        ):
            with self.subTest(bad), self.assertRaises(ValueError):
                validate_settings({**base, "corporate_actions": bad})

    def test_reports_without_the_section_are_raw(self):
        self.assertEqual(price_mode({}), RAW)
        self.assertEqual(price_mode({"corporate_actions": {"mode": "adjusted"}}), ADJUSTED)


class CoverageTests(unittest.TestCase):
    def test_uncovered_ranges(self):
        self.assertEqual(
            ca.uncovered([], "2026-01-01", "2026-01-31"), [("2026-01-01", "2026-01-31")]
        )
        ranges = [("2026-01-05", "2026-01-10"), ("2026-01-08", "2026-01-20")]
        self.assertEqual(
            ca.uncovered(ranges, "2026-01-01", "2026-01-31"),
            [("2026-01-01", "2026-01-04"), ("2026-01-21", "2026-01-31")],
        )
        self.assertEqual(
            ca.uncovered([("2025-12-01", "2026-02-01")], "2026-01-01", "2026-01-31"), []
        )
        self.assertEqual(
            ca.uncovered(
                [("2026-01-01", "2026-01-15"), ("2026-01-16", "2026-01-31")],
                "2026-01-01",
                "2026-01-31",
            ),
            [],
        )

    def test_coverage_is_saved_with_actions_even_without_events(self):
        with TemporaryDirectory() as folder:
            db = Path(folder) / "stocks.db"
            ca.save_actions(
                [], db, fetched_at="t1", coverage=[("2330", "2026-01-01", "2026-03-31")]
            )
            ca.save_actions(
                [],
                db,
                fetched_at="t2",
                coverage=[
                    ("2330", "2026-01-01", "2026-03-31"),
                    ("2317", "2026-02-01", "2026-02-28"),
                ],
            )
            self.assertEqual(
                ca.load_coverage(db),
                {"2317": [("2026-02-01", "2026-02-28")], "2330": [("2026-01-01", "2026-03-31")]},
            )
            self.assertEqual(
                ca.load_coverage(db, ["2330"]), {"2330": [("2026-01-01", "2026-03-31")]}
            )
            self.assertEqual(ca.load_coverage(Path(folder) / "missing.db"), {})


class PriceSourceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = Path(temp.name) / "config.toml"
        config.write_text(TEST_CONFIG, encoding="utf-8")
        self.settings = load_config(config)
        self.db = self.settings["storage"]["database_path"]
        prices = ["8.70", "8.72", "8.72", "10.15", "10.20"]
        save_records(
            [price_row(d, D(p)) for d, p in zip(DAYS, prices, strict=True)], db_path=self.db
        )

    def source(self, mode):
        return PriceSource({**self.settings, "corporate_actions": {"mode": mode}})

    def test_raw_mode_returns_original_prices(self):
        ca.save_actions(
            [REDUCTION], self.db, fetched_at="t", coverage=[("1441", "2026-01-01", "2026-12-31")]
        )
        source = self.source(RAW)
        records = source.load("1441", "1900-01-01", DAYS[-1])
        self.assertEqual(records[2]["close"], D("8.72"))
        self.assertEqual(
            source.describe(),
            {"mode": "raw", "rights": None, "actions": [], "uncovered": {}, "restarts": []},
        )

    def test_adjusted_mode_applies_only_events_up_to_the_analysis_date(self):
        ca.save_actions(
            [REDUCTION], self.db, fetched_at="t", coverage=[("1441", "2026-01-01", "2026-12-31")]
        )
        source = self.source(ADJUSTED)
        before = source.load("1441", "1900-01-01", DAYS[2])
        self.assertEqual(before[2]["close"], D("8.72"))
        self.assertEqual(source.describe()["actions"], [])
        after = source.load("1441", "1900-01-01", DAYS[-1])
        self.assertEqual(after[2]["close"].quantize(D("0.01")), D("10.13"))
        described = source.describe()
        self.assertEqual(
            [(a["stock_id"], a["effective_date"]) for a in described["actions"]],
            [("1441", DAYS[3])],
        )
        self.assertEqual(described["uncovered"], {})

    def test_periods_without_downloaded_actions_are_reported(self):
        ca.save_actions(
            [], self.db, fetched_at="t", coverage=[("1441", "2026-03-04", "2026-12-31")]
        )
        source = self.source(ADJUSTED)
        source.load("1441", "1900-01-01", DAYS[-1])
        self.assertEqual(source.describe()["uncovered"], {"1441": [["2026-03-02", "2026-03-03"]]})

    def test_history_matches_load_on_every_day(self):
        ca.save_actions(
            [REDUCTION], self.db, fetched_at="t", coverage=[("1441", "2026-01-01", "2026-12-31")]
        )
        source = self.source(ADJUSTED)
        raw = self.source(RAW).load("1441", "1900-01-01", DAYS[-1])
        history = source.history("1441", DAYS[0], DAYS[-1])
        for record in raw:
            history.append(record)
            self.assertEqual(history.rows, source.load("1441", "1900-01-01", record["date"]))


if __name__ == "__main__":
    unittest.main()
