import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from tw_stock_backtest.cli import fetch_stock


class TestFetchMonth(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "stat": "OK",
            "fields": ["日期", "收盤價"],
            "data": [["115/09/01", "100.00"]],
        }

    def make_response(self, payload):
        """建立可供 json.load 讀取的模擬回應。"""
        return io.BytesIO(
            json.dumps(payload).encode("utf-8")
        )

    def fetch(self):
        return fetch_stock.fetch_month(
            "2330",
            "20260901",
            timeout_seconds=12,
        )

    def test_success_and_request_parameters(self):
        with patch.object(
            fetch_stock,
            "urlopen",
            return_value=self.make_response(self.payload),
        ) as mock_urlopen:
            result = self.fetch()

        self.assertEqual(result, self.payload)
        mock_urlopen.assert_called_once()

        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)

        self.assertEqual(query["stockNo"], ["2330"])
        self.assertEqual(query["date"], ["20260901"])
        self.assertEqual(query["response"], ["json"])
        self.assertEqual(
            mock_urlopen.call_args.kwargs["timeout"],
            12,
        )

    def test_http_error_is_propagated(self):
        error = HTTPError(
            url="https://example.invalid",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

        with patch.object(
            fetch_stock,
            "urlopen",
            side_effect=error,
        ):
            with self.assertRaises(HTTPError):
                self.fetch()

    def test_connection_error_is_propagated(self):
        with patch.object(
            fetch_stock,
            "urlopen",
            side_effect=URLError("connection failed"),
        ):
            with self.assertRaises(URLError):
                self.fetch()

    def test_timeout_is_propagated(self):
        with patch.object(
            fetch_stock,
            "urlopen",
            side_effect=TimeoutError("request timed out"),
        ):
            with self.assertRaises(TimeoutError):
                self.fetch()

    def test_invalid_json_is_rejected(self):
        with patch.object(
            fetch_stock,
            "urlopen",
            return_value=io.BytesIO(b"<html>error</html>"),
        ):
            with self.assertRaises(json.JSONDecodeError):
                self.fetch()

    def test_non_object_json_is_rejected(self):
        with patch.object(
            fetch_stock,
            "urlopen",
            return_value=self.make_response([]),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "行情回應格式錯誤",
            ):
                self.fetch()

    def test_unsuccessful_status_is_rejected(self):
        payload = {
            "stat": "查無資料",
        }

        with patch.object(
            fetch_stock,
            "urlopen",
            return_value=self.make_response(payload),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "行情查詢未成功",
            ):
                self.fetch()

    def test_missing_fields_or_data_is_rejected(self):
        payloads = [
            {"stat": "OK", "data": [["example"]]},
            {"stat": "OK", "fields": ["日期"]},
            {
                "stat": "OK",
                "fields": [],
                "data": [["example"]],
            },
            {
                "stat": "OK",
                "fields": ["日期"],
                "data": [],
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                with patch.object(
                    fetch_stock,
                    "urlopen",
                    return_value=self.make_response(payload),
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "回應缺少欄位或行情資料",
                    ):
                        self.fetch()


class TestFetchCommandFailure(unittest.TestCase):
    def test_second_month_failure_does_not_save_partial_data(self):
        args = argparse.Namespace(
            config="unused.toml",
            stock="2330",
            start="2026-08-01",
            end="2026-09-22",
            show=False,
        )

        settings = {
            "download": {
                "timeout_seconds": 30,
                "request_interval_seconds": 3,
                "max_attempts": 1,
                "retry_wait_seconds": 3,
            },
            "storage": {
                "database_path": Path("unused.db"),
            },
        }

        first_month = {
            "stat": "OK",
            "fields": [
                "日期",
                "成交股數",
                "成交金額",
                "開盤價",
                "最高價",
                "最低價",
                "收盤價",
                "成交筆數",
            ],
            "data": [
                [
                    "115/08/03",
                    "1,000",
                    "100,000",
                    "100.00",
                    "101.00",
                    "99.00",
                    "100.00",
                    "10",
                ],
            ],
        }

        output = io.StringIO()

        with (
            patch.object(
                fetch_stock,
                "parse_arguments",
                return_value=args,
            ),
            patch.object(
                fetch_stock,
                "load_config",
                return_value=settings,
            ),
            patch.object(
                fetch_stock,
                "fetch_month",
                side_effect=[
                    first_month,
                    TimeoutError("second month timed out"),
                ],
            ) as mock_fetch,
            patch.object(
                fetch_stock,
                "save_records",
            ) as mock_save,
            patch.object(
                fetch_stock.time,
                "sleep",
            ) as mock_sleep,
            redirect_stdout(output),
        ):
            exit_code = fetch_stock.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(mock_fetch.call_count, 2)

        # 失敗時，不應保存第一個月的部分結果。
        mock_save.assert_not_called()

        # 測試不真的等待，但確認程式有要求設定的間隔。
        mock_sleep.assert_called_once_with(3)

        self.assertIn(
            "資料處理失敗",
            output.getvalue(),
        )
        self.assertIn(
            "second month timed out",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()