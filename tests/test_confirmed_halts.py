"""已確認停牌清單：從套件資料檔讀取並驗證；測試以臨時檔案檢查格式錯誤。"""

import contextlib
import io
import json
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.cli import inspect_db
from tw_stock_backtest.data.database import load_records, save_records
from tw_stock_backtest.data.market_events import CONFIRMED_HALTS, load_confirmed_halts

SOURCE = "https://www.twse.com.tw/example"


def entry(**changes):
    return {
        "stock_id": "1234",
        "start": "2026-03-02",
        "end": "2026-03-04",
        "reason": "測試停牌",
        "sources": [SOURCE],
        **changes,
    }


class ConfirmedHaltFileTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "halts.json"

    def load(self, *entries, schema_version=1):
        self.path.write_text(
            json.dumps({"schema_version": schema_version, "halts": list(entries)}), encoding="utf-8"
        )
        return load_confirmed_halts(self.path)

    def test_packaged_file_is_loaded_and_keeps_the_original_event(self):
        event = CONFIRMED_HALTS[("2317", "2025-07-30")]
        self.assertEqual(event["reason"], "重大訊息待公布，暫停交易")
        self.assertTrue(event["source"].startswith("https://investoredu.twse.com.tw/"))
        self.assertEqual(load_confirmed_halts(), CONFIRMED_HALTS)
        for event in CONFIRMED_HALTS.values():
            self.assertTrue(
                event["sources"] and all(url.startswith("https://") for url in event["sources"])
            )

    def test_range_is_expanded_to_every_calendar_day(self):
        halts = self.load(entry(start="2026-03-06", end="2026-03-09"))
        self.assertEqual(
            sorted(day for _, day in halts),
            ["2026-03-06", "2026-03-07", "2026-03-08", "2026-03-09"],
        )
        self.assertEqual(
            halts[("1234", "2026-03-07")],
            {"reason": "測試停牌", "source": SOURCE, "sources": [SOURCE]},
        )

    def test_invalid_entries_are_rejected_instead_of_skipped(self):
        cases = {
            "沒有來源": entry(sources=[]),
            "非 https 來源": entry(sources=["http://www.twse.com.tw/x"]),
            "代號格式": entry(stock_id="12345"),
            "空白原因": entry(reason=" "),
            "起訖顛倒": entry(start="2026-03-05", end="2026-03-04"),
            "日期格式": entry(start="2026/03/02"),
            "區間過長": entry(start="2025-01-01", end="2026-12-31"),
        }
        for name, bad in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.load(bad)

    def test_overlapping_ranges_and_unknown_schema_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "重疊"):
            self.load(entry(), entry(start="2026-03-04", end="2026-03-05"))
        with self.assertRaises(ValueError):
            self.load(entry(), schema_version=2)


# 合成行情：2026-03-02（一）～ 03-13（五）共 10 個行情日；1111 在 03-05～03-10 整列不存在。
DAYS = (
    "2026-03-02",
    "2026-03-03",
    "2026-03-04",
    "2026-03-05",
    "2026-03-06",
    "2026-03-09",
    "2026-03-10",
    "2026-03-11",
    "2026-03-12",
    "2026-03-13",
)
GAP = DAYS[3:7]
EVENT = {"reason": "測試停止買賣", "source": SOURCE, "sources": [SOURCE]}


def halt_range(start, end, stock_id="1111"):
    current, last, keys = date.fromisoformat(start), date.fromisoformat(end), {}
    while current <= last:
        keys[(stock_id, current.isoformat())] = EVENT
        current += timedelta(days=1)
    return keys


def price_row(stock_id, day, index):
    price = Decimal(100 + index)
    return {
        "stock_id": stock_id,
        "date": day,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1000,
        "turnover": 100000,
        "trade_count": 10,
    }


class HaltRowsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "data" / "stocks.db"
        rows = [price_row("2222", day, i) for i, day in enumerate(DAYS)]
        rows += [price_row("1111", day, i) for i, day in enumerate(DAYS) if day not in GAP]
        save_records(rows, db_path=self.db)
        # 涵蓋週末（03-07、03-08）的停牌區間。
        self.halts = halt_range("2026-03-05", "2026-03-10")

    def test_confirmed_halt_days_get_halt_rows_without_prices(self):
        records = load_records(
            "1111", DAYS[0], DAYS[-1], db_path=self.db, confirmed_halts=self.halts
        )
        self.assertEqual([r["date"] for r in records], list(DAYS))
        for record in records:
            if record["date"] in GAP:
                self.assertEqual([record[k] for k in ("open", "high", "low", "close")], [None] * 4)
                self.assertEqual(
                    (record["volume"], record["turnover"], record["trade_count"]), (0, 0, 0)
                )
        # 週末不是行情日，不補列。
        self.assertNotIn("2026-03-07", [r["date"] for r in records])

    def test_unconfirmed_gaps_and_other_ranges_stay_as_they_are(self):
        plain = load_records("1111", DAYS[0], DAYS[-1], db_path=self.db, confirmed_halts={})
        self.assertEqual(len(plain), len(DAYS) - len(GAP))
        inside = load_records("1111", DAYS[4], DAYS[5], db_path=self.db, confirmed_halts=self.halts)
        self.assertEqual([r["date"] for r in inside], [DAYS[4], DAYS[5]])
        other = load_records("2222", DAYS[0], DAYS[-1], db_path=self.db, confirmed_halts=self.halts)
        self.assertTrue(all(r["close"] is not None for r in other))

    def test_existing_rows_are_never_replaced(self):
        halts = halt_range(DAYS[0], DAYS[0])
        records = load_records("1111", DAYS[0], DAYS[0], db_path=self.db, confirmed_halts=halts)
        self.assertEqual(records[0]["close"], Decimal(100))

    def test_backtest_runs_through_the_halt_instead_of_failing(self):
        records = {
            sid: load_records(sid, DAYS[0], DAYS[-1], db_path=self.db, confirmed_halts=self.halts)
            for sid in ("1111", "2222")
        }
        costs = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        settings = {
            "short_window": 2,
            "long_window": 3,
            "volume_window": 2,
            "min_volume_ratio": Decimal("0"),
            "momentum_window": 2,
            "top_n": 2,
        }
        result = run_portfolio_backtest(
            records,
            DAYS[3],
            DAYS[-1],
            screening_settings=settings,
            initial_cash=Decimal("100000"),
            quantity=10,
            max_positions=2,
            cost_settings=costs,
            confirmed_halts=self.halts,
        )
        events = [(e["stock_id"], e["date"], e["market_status"]) for e in result["market_events"]]
        self.assertEqual(events, [("1111", day, "CONFIRMED_HALT") for day in GAP])
        self.assertEqual(result["equity_curve"][-1]["date"], DAYS[-1])
        # 沒有已確認停牌時，同樣的資料仍因缺少行情列而失敗。
        plain = {
            sid: load_records(sid, DAYS[0], DAYS[-1], db_path=self.db, confirmed_halts={})
            for sid in ("1111", "2222")
        }
        with self.assertRaisesRegex(ValueError, "1111 缺少 2026-03-(05|06|09|10) 的行情列"):
            run_portfolio_backtest(
                plain,
                DAYS[3],
                DAYS[-1],
                screening_settings=settings,
                initial_cash=Decimal("100000"),
                quantity=10,
                max_positions=2,
                cost_settings=costs,
                confirmed_halts={},
            )

    def test_inspect_db_marks_gaps_covered_by_confirmed_halts(self):
        config = self.root / "config.toml"
        config.write_text(TEST_CONFIG.replace("AAA", "2222"), encoding="utf-8")

        def run(halts):
            output = io.StringIO()
            with (
                patch.object(inspect_db, "CONFIRMED_HALTS", halts),
                patch.object(sys, "argv", ["inspect_db", "--config", str(config), "--gaps"]),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(inspect_db.main(), 0)
            return output.getvalue()

        line = "1111：2026-03-04 → 2026-03-11，相隔 7 個日曆日，其他股票有 4 個行情日"
        self.assertIn(line + "（已確認停牌）", run(self.halts))
        self.assertIn(line + "\n", run({}))
        # 只涵蓋部分缺漏日時不註明。
        self.assertIn(line + "\n", run(halt_range("2026-03-05", "2026-03-06")))


if __name__ == "__main__":
    unittest.main()
