import sqlite3
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from database import save_records, load_records


class TestSaveRecords(unittest.TestCase):
    def setUp(self):
        # 每個測試使用獨立的暫存資料夾。
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.db_path = Path(self.temp_dir.name) / "stocks.db"

        # 人工設計的測試資料，不代表真實行情。
        self.record = {
            "stock_id": "2330",
            "date": "2025-01-02",
            "open": Decimal("100.10"),
            "high": Decimal("110.00"),
            "low": Decimal("99.00"),
            "close": Decimal("105.25"),
            "volume": 1000,
            "turnover": 105250,
            "trade_count": 10,
        }

    def read_rows(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            return connection.execute("""
                SELECT
                    stock_id, date, open, high, low, close,
                    volume, turnover, trade_count
                FROM stock_prices
                ORDER BY stock_id, date
            """).fetchall()

    def test_insert_record(self):
        count = save_records([self.record], self.db_path)

        self.assertEqual(count, 1)
        self.assertEqual(
            self.read_rows(),
            [(
                "2330",
                "2025-01-02",
                "100.10",
                "110.00",
                "99.00",
                "105.25",
                1000,
                105250,
                10,
            )],
        )

    def test_repeated_save_does_not_duplicate(self):
        save_records([self.record], self.db_path)
        save_records([self.record], self.db_path)

        self.assertEqual(len(self.read_rows()), 1)

    def test_existing_record_is_updated(self):
        save_records([self.record], self.db_path)

        updated = self.record.copy()
        updated["close"] = Decimal("106.50")
        updated["turnover"] = 106500

        save_records([updated], self.db_path)

        rows = self.read_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], "106.50")
        self.assertEqual(rows[0][7], 106500)

    def test_different_stocks_on_same_date(self):
        other = self.record.copy()
        other["stock_id"] = "2317"

        save_records([self.record, other], self.db_path)

        rows = self.read_rows()

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row[0] for row in rows},
            {"2330", "2317"},
        )

    def test_missing_prices_are_saved_as_null(self):
        missing = self.record.copy()

        for key in ("open", "high", "low", "close"):
            missing[key] = None

        for key in ("volume", "turnover", "trade_count"):
            missing[key] = 0

        save_records([missing], self.db_path)

        row = self.read_rows()[0]
        self.assertEqual(row[2:6], (None, None, None, None))

    def test_failed_batch_is_rolled_back(self):
        # 先保存一筆，作為原有資料。
        save_records([self.record], self.db_path)

        valid = self.record.copy()
        valid["date"] = "2025-01-03"

        invalid = self.record.copy()
        invalid["date"] = "2025-01-06"
        invalid["volume"] = -1

        # 負成交股數違反資料表的 CHECK 限制。
        with self.assertRaises(sqlite3.IntegrityError):
            save_records([valid, invalid], self.db_path)

        # 這批的第一筆也應撤銷，原有資料則保留。
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "2025-01-02")

    def test_load_filters_and_sorts_records(self):
        first = self.record.copy()

        second = self.record.copy()
        second["date"] = "2025-01-03"

        outside = self.record.copy()
        outside["date"] = "2025-01-06"

        other_stock = self.record.copy()
        other_stock["stock_id"] = "2317"

        # 刻意不依日期順序寫入。
        save_records(
            [second, outside, other_stock, first],
            self.db_path,
        )

        result = load_records(
            "2330",
            "2025-01-02",
            "2025-01-03",
            self.db_path,
        )

        # 只取指定股票與區間，並按照日期排序。
        self.assertEqual(result, [first, second])

        # 價格確實恢復成 Decimal。
        self.assertIsInstance(result[0]["close"], Decimal)

    def test_load_empty_range(self):
        save_records([self.record], self.db_path)

        result = load_records(
            "2330",
            "2025-02-01",
            "2025-02-28",
            self.db_path,
        )

        self.assertEqual(result, [])

    def test_load_preserves_missing_prices(self):
        missing = self.record.copy()

        for key in ("open", "high", "low", "close"):
            missing[key] = None

        for key in ("volume", "turnover", "trade_count"):
            missing[key] = 0

        save_records([missing], self.db_path)

        result = load_records(
            "2330",
            "2025-01-02",
            "2025-01-02",
            self.db_path,
        )

        self.assertEqual(result, [missing])

if __name__ == "__main__":
    unittest.main()