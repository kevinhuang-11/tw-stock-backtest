"""行情庫的索引與查詢計畫；全部使用臨時資料庫與合成資料。"""
import contextlib
import io
import sqlite3
import sys
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.cli import migrate_db
from tw_stock_backtest.data import database
from tw_stock_backtest.data.database import (
    INDEXES, SCHEMA_VERSION, load_records, migrate_database, read_schema_version, save_records,
)

# 舊版程式（schema 版本機制之前）建立行情表的原文；user_version 維持 0。
LEGACY_TABLE = """
    CREATE TABLE IF NOT EXISTS stock_prices (
        stock_id TEXT NOT NULL,
        date TEXT NOT NULL,
        open TEXT,
        high TEXT,
        low TEXT,
        close TEXT,
        volume INTEGER NOT NULL CHECK (volume >= 0),
        turnover INTEGER NOT NULL CHECK (turnover >= 0),
        trade_count INTEGER NOT NULL CHECK (trade_count >= 0),
        PRIMARY KEY (stock_id, date)
    )
"""

DATES = ("2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06")


def synthetic_records(stocks=("2330", "2317", "1101"), dates=DATES, missing=(("1101", "2026-03-04"),)):
    records = []
    for stock_id in stocks:
        for day in dates:
            price = None if (stock_id, day) in missing else Decimal("10.5")
            records.append({"stock_id": stock_id, "date": day, "open": price, "high": price,
                            "low": price, "close": price, "volume": 100, "turnover": 1000,
                            "trade_count": 3})
    return records


# 依日期的查詢：行情日清單、某日之後的行情日數、某日的所有股票、缺價列。
DATE_QUERIES = {
    "market_dates": ("SELECT DISTINCT date FROM stock_prices ORDER BY date", ()),
    "market_days_after": ("SELECT COUNT(DISTINCT date) FROM stock_prices WHERE date > ?", ("2026-03-03",)),
    "rows_on_date": ("SELECT stock_id, close FROM stock_prices WHERE date = ?", ("2026-03-04",)),
    "missing_closes": ("SELECT stock_id, date FROM stock_prices WHERE close IS NULL ORDER BY stock_id, date", ()),
}


def plan(connection, sql, params=()):
    return " / ".join(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params))


class IndexTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "stocks.db"
        save_records(synthetic_records(), db_path=self.db_path)

    def connect(self):
        return closing(sqlite3.connect(self.db_path))

    def drop_indexes(self):
        # 刪除後要換新連線：sqlite3 模組會快取相同 SQL 的已編譯敘述，舊連線可能沿用舊計畫。
        with self.connect() as connection:
            for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_stock_prices_%'"
            ).fetchall():
                connection.execute(f'DROP INDEX "{name}"')

    def test_save_creates_the_indexes(self):
        with self.connect() as connection:
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'stock_prices'")}
        self.assertTrue({"idx_stock_prices_date", "idx_stock_prices_missing_close"} <= names)
        self.assertEqual(len(INDEXES), 2)

    def test_date_queries_scan_without_indexes_and_search_with_them(self):
        with self.connect() as connection:
            indexed = {name: plan(connection, *query) for name, query in DATE_QUERIES.items()}
        self.drop_indexes()
        with self.connect() as connection:
            plain = {name: plan(connection, *query) for name, query in DATE_QUERIES.items()}

        # 沒有索引：要掃全表或主鍵，行情日清單還需要臨時 B-tree 去重。
        self.assertIn("TEMP B-TREE", plain["market_dates"])
        self.assertIn("SCAN", plain["market_days_after"])
        self.assertIn("SCAN stock_prices", plain["rows_on_date"])
        self.assertNotIn("idx_stock_prices_missing_close", plain["missing_closes"])

        self.assertIn("idx_stock_prices_date", indexed["market_dates"])
        self.assertNotIn("TEMP B-TREE", indexed["market_dates"])
        self.assertIn("SEARCH stock_prices USING COVERING INDEX idx_stock_prices_date", indexed["market_days_after"])
        self.assertIn("SEARCH stock_prices USING INDEX idx_stock_prices_date (date=?)", indexed["rows_on_date"])
        self.assertIn("idx_stock_prices_missing_close", indexed["missing_closes"])

    def test_queries_by_stock_keep_using_the_primary_key(self):
        with self.connect() as connection:
            result = plan(connection, "SELECT * FROM stock_prices WHERE stock_id = ? AND date >= ? AND date <= ? "
                                      "ORDER BY date", ("2330", DATES[0], DATES[-1]))
        self.assertIn("SEARCH stock_prices USING INDEX sqlite_autoindex_stock_prices_1", result)

    def test_indexes_return_the_same_rows(self):
        with self.connect() as connection:
            indexed = {name: connection.execute(*query).fetchall() for name, query in DATE_QUERIES.items()}
        self.drop_indexes()
        with self.connect() as connection:
            plain = {name: connection.execute(*query).fetchall() for name, query in DATE_QUERIES.items()}
        self.assertEqual(sorted(indexed["rows_on_date"]), sorted(plain["rows_on_date"]))
        for name in ("market_dates", "market_days_after", "missing_closes"):
            self.assertEqual(indexed[name], plain[name], name)
        self.assertEqual(indexed["missing_closes"], [("1101", "2026-03-04")])

    def test_saving_twice_keeps_one_copy_of_each_index(self):
        save_records(synthetic_records(), db_path=self.db_path)
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_stock_prices_%'"
            ).fetchone()[0]
        self.assertEqual(count, 2)


def all_rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT * FROM stock_prices ORDER BY stock_id, date").fetchall()


def index_names(path):
    with closing(sqlite3.connect(path)) as connection:
        return sorted(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_stock_prices_%'"))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db_path = self.root / "stocks.db"

    def make_legacy_database(self):
        # 依舊版程式的寫法建立：只有表、沒有索引、user_version 為 0，含缺價列。
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(LEGACY_TABLE)
            connection.executemany(
                "INSERT INTO stock_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["stock_id"], r["date"], *(None if r[k] is None else str(r[k]) for k in ("open", "high", "low", "close")),
                  r["volume"], r["turnover"], r["trade_count"]) for r in synthetic_records()])
        return all_rows(self.db_path)

    def test_legacy_database_is_upgraded_without_changing_any_row(self):
        before = self.make_legacy_database()
        self.assertEqual(read_schema_version(self.db_path), 0)
        self.assertEqual(index_names(self.db_path), [])

        self.assertEqual(migrate_database(self.db_path), (0, SCHEMA_VERSION))
        self.assertEqual(all_rows(self.db_path), before)
        self.assertEqual(index_names(self.db_path), ["idx_stock_prices_date", "idx_stock_prices_missing_close"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_migration_can_run_again_without_changes(self):
        self.make_legacy_database()
        migrate_database(self.db_path)
        rows, indexes = all_rows(self.db_path), index_names(self.db_path)
        self.assertEqual(migrate_database(self.db_path), (SCHEMA_VERSION, SCHEMA_VERSION))
        self.assertEqual((all_rows(self.db_path), index_names(self.db_path)), (rows, indexes))

    def test_saving_into_a_legacy_database_upgrades_it_first(self):
        before = self.make_legacy_database()
        extra = synthetic_records(stocks=("2454",), missing=())
        save_records(extra, db_path=self.db_path)
        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)
        self.assertEqual(len(all_rows(self.db_path)), len(before) + len(extra))
        self.assertEqual(load_records("1101", DATES[0], DATES[-1], db_path=self.db_path)[2]["close"], None)

    def test_new_database_starts_at_the_current_version(self):
        save_records(synthetic_records(), db_path=self.db_path)
        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)

    def test_failed_step_rolls_back_and_keeps_the_old_version(self):
        before = self.make_legacy_database()
        broken = database.MIGRATIONS + ((SCHEMA_VERSION + 1, (
            "CREATE INDEX idx_stock_prices_extra ON stock_prices(volume)",
            "THIS IS NOT SQL",
        )),)
        with patch.object(database, "MIGRATIONS", broken), \
                patch.object(database, "SCHEMA_VERSION", SCHEMA_VERSION + 1):
            with self.assertRaises(sqlite3.Error):
                migrate_database(self.db_path)
        # 已完成的版本保留；失敗的版本連同它建立的索引一起回滾。
        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)
        self.assertNotIn("idx_stock_prices_extra", index_names(self.db_path))
        self.assertEqual(all_rows(self.db_path), before)

    def test_newer_database_is_refused(self):
        self.make_legacy_database()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(ValueError, "比程式支援的"):
            save_records(synthetic_records(), db_path=self.db_path)

    def test_reading_the_version_never_creates_a_file(self):
        self.assertIsNone(read_schema_version(self.db_path))
        self.assertFalse(self.db_path.exists())

    def run_cli(self, *arguments):
        config = self.root / "config.toml"
        config.write_text(TEST_CONFIG, encoding="utf-8")
        output = io.StringIO()
        with patch.object(sys, "argv", ["migrate_db", "--config", str(config), *arguments]), \
                contextlib.redirect_stdout(output):
            code = migrate_db.main()
        return code, output.getvalue()

    def test_cli_check_does_not_modify_and_upgrade_reports_versions(self):
        self.db_path = self.root / "data" / "stocks.db"
        self.db_path.parent.mkdir()
        self.make_legacy_database()
        code, output = self.run_cli("--check")
        self.assertEqual(code, 2)
        self.assertIn("schema 版本：0", output)
        self.assertEqual(read_schema_version(self.db_path), 0)

        code, output = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn(f"schema 版本：0 → {SCHEMA_VERSION}", output)
        self.assertEqual(self.run_cli("--check")[0], 0)

    def test_cli_does_not_create_a_missing_database(self):
        code, output = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("行情庫不存在", output)
        self.assertFalse((self.root / "data" / "stocks.db").exists())


if __name__ == "__main__":
    unittest.main()
