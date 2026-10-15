"""行情庫的索引與查詢計畫；全部使用臨時資料庫與合成資料。"""
import sqlite3
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.data.database import INDEXES, save_records

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


if __name__ == "__main__":
    unittest.main()
