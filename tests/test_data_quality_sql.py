"""資料品質查詢改由 SQL 計算後，結果必須與原本的 Python 寫法完全一致。

參考實作是改寫前 data_status.py 的 Python 版本（逐筆讀出日期後在 Python 分組、二分搜尋），
原樣保留在這裡；比對用固定亂數種子產生的合成行情庫，包含缺價、停在舊日期、缺口與只有一筆的股票。
"""
import contextlib
import io
import random
import re
import sqlite3
import sys
import unittest
from bisect import bisect_right
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.cli import inspect_db

from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data_status import (
    market_dates, market_lag, price_gaps, price_overview, stock_quality,
)


# ---- 改寫前的 Python 實作（參考答案） ----

def python_price_summary(path):
    with closing(sqlite3.connect(path)) as db:
        dates = tuple(row[0] for row in db.execute("SELECT DISTINCT date FROM stock_prices ORDER BY date"))
        latest = dict(db.execute("SELECT stock_id, MAX(date) FROM stock_prices GROUP BY stock_id"))
    return dates, latest


def python_market_lag(day, dates):
    if not day or not dates:
        return None
    return len(dates) - bisect_right(dates, day)


def python_overview(path):
    dates, latest = python_price_summary(path)
    newest = dates[-1] if dates else None
    behind = sorted(day for day in latest.values() if day != newest)
    return newest, len(latest), len(behind), behind[0] if behind else None


def python_rows(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT stock_id, date, close FROM stock_prices ORDER BY stock_id, date").fetchall()


def python_stock_quality(path):
    dates, _ = python_price_summary(path)
    result = {}
    for stock_id, day, close in python_rows(path):
        row = result.setdefault(stock_id, {"stock_id": stock_id, "rows": 0, "first_date": day,
                                           "last_date": day, "missing_close": 0})
        row["rows"] += 1
        row["last_date"] = day
        row["missing_close"] += close is None
    for row in result.values():
        row["market_lag"] = python_market_lag(row["last_date"], dates)
    return [result[key] for key in sorted(result)]


def python_gaps(path, stock_id=None, calendar_days=20):
    dates, _ = python_price_summary(path)
    position = {day: index for index, day in enumerate(dates)}
    gaps, previous = [], {}
    for sid, day, _ in python_rows(path):
        if stock_id is not None and sid != stock_id:
            continue
        if sid in previous:
            before = previous[sid]
            missing = position[day] - position[before] - 1
            span = (date.fromisoformat(day) - date.fromisoformat(before)).days
            if missing > 0 or span > calendar_days:
                gaps.append({"stock_id": sid, "previous_date": before, "date": day,
                             "missing_market_days": missing, "calendar_days": span})
        previous[sid] = day
    return gaps


# ---- 合成行情庫 ----

def random_database(path, seed):
    """約 40 個平日；部分股票提早停止、中間缺列、收盤為 NULL，並有一段全庫空白。"""
    rng = random.Random(seed)
    days, current = [], date(2026, 1, 5)
    while len(days) < 40:
        if current.weekday() < 5 and not (date(2026, 2, 9) <= current <= date(2026, 3, 6)):
            days.append(current.isoformat())
        current += timedelta(days=1)
    records = []
    for number in range(rng.randint(5, 12)):
        stock_id = str(2000 + number)
        start = rng.randint(0, 10)
        end = rng.randint(start, len(days) - 1) if rng.random() < .4 else len(days) - 1
        for day in days[start:end + 1]:
            if rng.random() < .08:
                continue  # 缺少整列
            price = None if rng.random() < .06 else Decimal("10.25")
            records.append({"stock_id": stock_id, "date": day, "open": price, "high": price, "low": price,
                            "close": price, "volume": rng.randint(0, 9), "turnover": 0, "trade_count": 0})
    records.append({"stock_id": "9999", "date": days[3], "open": None, "high": None, "low": None,
                    "close": None, "volume": 0, "turnover": 0, "trade_count": 0})
    save_records(records, db_path=path)
    return days


class SqlMatchesPythonTests(unittest.TestCase):
    SEEDS = range(12)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def databases(self):
        for seed in self.SEEDS:
            path = self.root / f"seed{seed}.db"
            days = random_database(path, seed)
            yield seed, path, days

    def test_overview_and_market_dates(self):
        for seed, path, _ in self.databases():
            with self.subTest(seed=seed):
                self.assertEqual(tuple(price_overview(path)), python_overview(path))
                self.assertEqual(market_dates(path), python_price_summary(path)[0])

    def test_market_lag_for_every_kind_of_day(self):
        for seed, path, days in self.databases():
            dates = python_price_summary(path)[0]
            probes = [*days, "2025-12-31", "2026-02-14", "2026-12-31", None, ""]
            for day in probes:
                with self.subTest(seed=seed, day=day):
                    self.assertEqual(market_lag(path, day), python_market_lag(day, dates))

    def test_stock_quality(self):
        for seed, path, _ in self.databases():
            with self.subTest(seed=seed):
                self.assertEqual(stock_quality(path), python_stock_quality(path))

    def test_gaps_with_lag_window_function(self):
        for seed, path, _ in self.databases():
            for calendar_days in (5, 20):
                with self.subTest(seed=seed, calendar_days=calendar_days):
                    self.assertEqual(price_gaps(path, calendar_days=calendar_days),
                                     python_gaps(path, calendar_days=calendar_days))
            with self.subTest(seed=seed, stock="2001"):
                self.assertEqual(price_gaps(path, stock_id="2001"), python_gaps(path, stock_id="2001"))

    def test_random_databases_cover_the_interesting_cases(self):
        # 確認比對的資料真的包含缺價、落後、缺口與長期空白，不是全部平順的資料。
        found = {"missing_close": 0, "behind": 0, "gap": 0, "long_break": 0}
        for _, path, _ in self.databases():
            quality = python_stock_quality(path)
            found["missing_close"] += sum(row["missing_close"] for row in quality)
            found["behind"] += sum(row["market_lag"] > 0 for row in quality)
            gaps = python_gaps(path)
            found["gap"] += sum(gap["missing_market_days"] > 0 for gap in gaps)
            found["long_break"] += sum(gap["missing_market_days"] == 0 for gap in gaps)
        self.assertTrue(all(found.values()), found)


class EmptyAndMissingDatabaseTests(unittest.TestCase):
    def test_missing_file_and_empty_table(self):
        with TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.db"
            self.assertEqual(price_overview(missing), (None, 0, 0, None))
            self.assertEqual((market_dates(missing), stock_quality(missing), price_gaps(missing)), ((), [], []))
            self.assertIsNone(market_lag(missing, "2026-01-05"))
            self.assertFalse(missing.exists())

            empty = Path(folder) / "empty.db"
            save_records([], db_path=empty)
            self.assertEqual(tuple(price_overview(empty)), python_overview(empty))
            self.assertEqual(market_lag(empty, "2026-01-05"), python_market_lag("2026-01-05", ()))
            self.assertEqual((stock_quality(empty), price_gaps(empty)), ([], []))

    def test_queries_never_read_prices_as_numbers(self):
        # SQL 只處理日期、筆數與 NULL；價格欄位只出現在 IS NULL 判斷中。
        from tw_stock_backtest import data_status
        for name in ("OVERVIEW_SQL", "STOCK_QUALITY_SQL", "GAPS_SQL"):
            sql = getattr(data_status, name)
            code = "\n".join(line.split("--")[0] for line in sql.splitlines())
            uses = re.findall(r"(?<![\w.])(open|high|low|close)\b(?:\s+IS\s+NULL)?", code)
            plain = re.findall(r"(?<![\w.])(open|high|low|close)\b(?!\s+IS\s+NULL)", code)
            self.assertEqual(plain, [], name)
            if name == "STOCK_QUALITY_SQL":
                self.assertEqual(uses, ["close"])


class InspectDbTests(unittest.TestCase):
    def test_summary_notes_missing_closes_lag_and_lists_gaps(self):
        def row(stock_id, day, close=Decimal("10")):
            return {"stock_id": stock_id, "date": day, "open": close, "high": close, "low": close,
                    "close": close, "volume": 1, "turnover": 1, "trade_count": 1}
        days = ("2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-04-06")
        with TemporaryDirectory() as folder:
            config = Path(folder) / "config.toml"
            config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
            save_records([row("2330", day) for day in days]
                         + [row("2317", days[0]), row("2317", days[2], None), row("2317", days[3])]
                         + [row("1101", day) for day in days[:2]],
                         db_path=Path(folder) / "data" / "stocks.db")
            output = io.StringIO()
            with patch.object(sys, "argv", ["inspect_db", "--config", str(config), "--gaps"]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(inspect_db.main(), 0)
        text = output.getvalue()
        self.assertIn("1101：2 筆，2026-03-02 ～ 2026-03-03（落後 3 個行情日）", text)
        self.assertIn("2317：3 筆，2026-03-02 ～ 2026-03-05（收盤價缺失 1 筆，落後 1 個行情日）", text)
        self.assertIn("2330：5 筆，2026-03-02 ～ 2026-04-06\n", text)
        self.assertIn("行情缺口：2 處", text)
        self.assertIn("2317：2026-03-02 → 2026-03-04，相隔 2 個日曆日，其他股票有 1 個行情日", text)
        self.assertIn("2330：2026-03-05 → 2026-04-06，相隔 32 個日曆日，全庫都沒有行情", text)


if __name__ == "__main__":
    unittest.main()
