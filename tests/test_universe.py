import unittest
from datetime import date
from html import escape
from tw_stock_backtest.data.universe import parse_listed_stocks
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.data.universe import (
    load_universe,
    save_universe,
)


class TestListedStocks(unittest.TestCase):
    def setUp(self):
        self.headers = [
            "有價證券代號及名稱",
            "國際證券辨識號碼(ISIN Code)",
            "上市日",
            "市場別",
            "產業別",
            "CFICode",
            "備註",
        ]

        self.row = [
            "2330　台積電",
            "TW0002330008",
            "1994/09/05",
            "上市",
            "半導體業",
            "ESVUFR",
            "",
        ]

        self.as_of = date(2026, 9, 22)

    def make_html(self, rows, headers=None):
        if headers is None:
            headers = self.headers

        all_rows = [headers, *rows]

        return "<table>" + "".join(
            "<tr>"
            + "".join(
                f"<td>{escape(value)}</td>"
                for value in row
            )
            + "</tr>"
            for row in all_rows
        ) + "</table>"

    def parse(self, rows, headers=None):
        return parse_listed_stocks(
            self.make_html(rows, headers),
            as_of=self.as_of,
        )

    def test_normal_stock(self):
        result = self.parse([self.row])

        self.assertEqual(
            result,
            [{
                "stock_id": "2330",
                "name": "台積電",
                "market": "上市",
                "industry": "半導體業",
                "listed_date": "1994-09-05",
                "cfi_code": "ESVUFR",
            }],
        )

    def test_excludes_other_security_types_and_markets(self):
        rows = [self.row]

        for code, cfi, market in (
            ("0050", "CEOGEU", "上市"),
            ("2881A", "EPNRAR", "上市"),
            ("123456", "RWSCCA", "上市"),
            ("6488", "ESVUFR", "上櫃"),
        ):
            row = self.row.copy()
            row[0] = f"{code}　測試證券"
            row[3] = market
            row[5] = cfi
            rows.append(row)

        result = self.parse(rows)

        self.assertEqual(
            [stock["stock_id"] for stock in result],
            ["2330"],
        )

    def test_reordered_columns(self):
        normal = self.parse([self.row])

        reordered = self.parse(
            [self.row[::-1]],
            headers=self.headers[::-1],
        )

        self.assertEqual(reordered, normal)

    def test_duplicate_stock_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse([self.row, self.row.copy()])

    def test_invalid_pages_are_rejected(self):
        for html in (
            "",
            "<html>服務暫時無法使用</html>",
            self.make_html([]),
        ):
            with self.subTest(html=html):
                with self.assertRaises(ValueError):
                    parse_listed_stocks(
                        html,
                        as_of=self.as_of,
                    )

    def test_future_listing_is_excluded_and_result_is_sorted(self):
        earlier = self.row.copy()
        earlier[0] = "1101　測試公司"

        future = self.row.copy()
        future[0] = "9999　尚未上市"
        future[2] = "2099/01/01"

        result = self.parse([self.row, future, earlier])

        self.assertEqual(
            [stock["stock_id"] for stock in result],
            ["1101", "2330"],
        )
    def test_save_and_load_universe(self):
        stocks = self.parse([self.row])

        with TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "listed_stocks.json"

            saved = save_universe(stocks, path)
            loaded = load_universe(path)

            self.assertEqual(loaded, saved)
            self.assertEqual(loaded["stocks"], stocks)
            self.assertEqual(loaded["count"], 1)

            # 確認中文名稱直接保存，方便人工查看。
            self.assertIn(
                "台積電",
                path.read_text(encoding="utf-8"),
            )

    def test_invalid_update_preserves_existing_file(self):
        stocks = self.parse([self.row])

        with TemporaryDirectory() as directory:
            path = Path(directory) / "listed_stocks.json"

            save_universe(stocks, path)
            original = path.read_bytes()

            with self.assertRaises(ValueError):
                save_universe([], path)

            self.assertEqual(path.read_bytes(), original)

    def test_incorrect_count_is_rejected(self):
        stocks = self.parse([self.row])

        with TemporaryDirectory() as directory:
            path = Path(directory) / "listed_stocks.json"

            snapshot = save_universe(stocks, path)
            snapshot["count"] = 999

            path.write_text(
                json.dumps(snapshot, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_universe(path)

    def test_corrupted_json_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "listed_stocks.json"
            path.write_text("{broken", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_universe(path)


if __name__ == "__main__":
    unittest.main()