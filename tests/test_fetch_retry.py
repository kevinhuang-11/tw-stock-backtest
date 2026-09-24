import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import call, patch
from urllib.error import HTTPError

from tw_stock_backtest.cli import fetch_stock


class TestFetchRetry(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "stat": "OK",
            "fields": ["日期"],
            "data": [["115/09/01"]],
        }

    def fetch(self):
        return fetch_stock.fetch_month_with_retry(
            "2330",
            "20260901",
            timeout_seconds=30,
            max_attempts=3,
            retry_wait_seconds=3,
        )

    def make_http_error(self, code):
        return HTTPError(
            url="https://example.invalid",
            code=code,
            msg="test error",
            hdrs=None,
            fp=None,
        )

    def test_success_does_not_retry(self):
        with (
            patch.object(
                fetch_stock,
                "fetch_month",
                return_value=self.payload,
            ) as mock_fetch,
            patch.object(
                fetch_stock.time,
                "sleep",
            ) as mock_sleep,
        ):
            result = self.fetch()

        self.assertEqual(result, self.payload)
        mock_fetch.assert_called_once()
        mock_sleep.assert_not_called()

    def test_timeout_then_success(self):
        with (
            patch.object(
                fetch_stock,
                "fetch_month",
                side_effect=[
                    TimeoutError("temporary timeout"),
                    self.payload,
                ],
            ) as mock_fetch,
            patch.object(
                fetch_stock.time,
                "sleep",
            ) as mock_sleep,
            redirect_stdout(io.StringIO()),
        ):
            result = self.fetch()

        self.assertEqual(result, self.payload)
        self.assertEqual(mock_fetch.call_count, 2)
        mock_sleep.assert_called_once_with(3)

    def test_503_then_success(self):
        with (
            patch.object(
                fetch_stock,
                "fetch_month",
                side_effect=[
                    self.make_http_error(503),
                    self.payload,
                ],
            ) as mock_fetch,
            patch.object(
                fetch_stock.time,
                "sleep",
            ) as mock_sleep,
            redirect_stdout(io.StringIO()),
        ):
            result = self.fetch()

        self.assertEqual(result, self.payload)
        self.assertEqual(mock_fetch.call_count, 2)
        mock_sleep.assert_called_once_with(3)

    def test_stops_after_max_attempts(self):
        with (
            patch.object(
                fetch_stock,
                "fetch_month",
                side_effect=TimeoutError("still unavailable"),
            ) as mock_fetch,
            patch.object(
                fetch_stock.time,
                "sleep",
            ) as mock_sleep,
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(TimeoutError):
                self.fetch()

        self.assertEqual(mock_fetch.call_count, 3)

        # 只在還有下一次嘗試時等待，不在最後一次失敗後等待。
        self.assertEqual(
            mock_sleep.call_args_list,
            [call(3), call(6)],
        )

    def test_non_retryable_http_errors(self):
        for code in (400, 403, 404, 429):
            with self.subTest(code=code):
                with (
                    patch.object(
                        fetch_stock,
                        "fetch_month",
                        side_effect=self.make_http_error(code),
                    ) as mock_fetch,
                    patch.object(
                        fetch_stock.time,
                        "sleep",
                    ) as mock_sleep,
                ):
                    with self.assertRaises(HTTPError):
                        self.fetch()

                mock_fetch.assert_called_once()
                mock_sleep.assert_not_called()

    def test_invalid_response_does_not_retry(self):
        with (
            patch.object(
                fetch_stock,
                "fetch_month",
                side_effect=ValueError("回應缺少欄位或行情資料"),
            ) as mock_fetch,
            patch.object(
                fetch_stock.time,
                "sleep",
            ) as mock_sleep,
        ):
            with self.assertRaises(ValueError):
                self.fetch()

        mock_fetch.assert_called_once()
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()