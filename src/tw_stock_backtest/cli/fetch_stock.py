import argparse
import json
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.date_range import (
    filter_records,
    month_starts,
    parse_date_range,
)
from tw_stock_backtest.data.stock_data import normalize_row


class NoMarketData(ValueError):
    """來源明確表示無資料；仍相容既有 ValueError 處理。"""


def fetch_month(stock_id, month, *, timeout_seconds):
    """下載指定股票、指定月份的原始行情。"""
    params = {
        "stockNo": stock_id,
        "date": month,
        "response": "json",
    }

    query = urlencode(params)
    url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?{query}"

    request = Request(
        url,
        headers={"User-Agent": "tw-stock-backtest/0.1"},
    )

    with urlopen(request, timeout=timeout_seconds) as response:
        result = json.load(response)

    if not isinstance(result, dict):
        raise ValueError("行情回應格式錯誤，預期為 JSON 物件")

    if result.get("stat") in ("查無資料", "很抱歉，沒有符合條件的資料!"):
        raise NoMarketData("行情查詢未成功：來源明確回覆無資料")

    if result.get("stat") != "OK":
        raise ValueError(
            f"行情查詢未成功：{result.get('stat')}"
        )

    if not result.get("fields") or not result.get("data"):
        raise ValueError("回應缺少欄位或行情資料")

    return result

def fetch_month_with_retry(
    stock_id,
    month,
    *,
    timeout_seconds,
    max_attempts,
    retry_wait_seconds,
):
    """遇到指定的暫時性錯誤時，有限次數重試。"""
    for name, value in (
        ("max_attempts", max_attempts),
        ("retry_wait_seconds", retry_wait_seconds),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise ValueError(f"{name} 必須是正整數")

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_month(
                stock_id,
                month,
                timeout_seconds=timeout_seconds,
            )

        except HTTPError as error:
            # HTTPError 是 URLError 的子類別，必須先處理。
            if (
                error.code not in (502, 503, 504)
                or attempt == max_attempts
            ):
                raise

            message = f"HTTP {error.code}"

        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == max_attempts:
                raise

            message = str(error)

        wait_seconds = retry_wait_seconds * (2 ** (attempt - 1))

        print(
            f"查詢失敗：{message}。"
            f"等待 {wait_seconds} 秒後進行第"
            f" {attempt + 1}/{max_attempts} 次嘗試。",
            flush=True,
        )

        time.sleep(wait_seconds)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="下載指定股票與日期區間的行情，存入 SQLite"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置，預設使用專案中的 config.toml",
    )
    parser.add_argument(
        "--stock",
        required=True,
        help="股票代號，例如 2330",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="開始日期，例如 2026-07-01",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="結束日期，例如 2026-09-22",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="顯示全部整理後行情",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        settings = load_config(args.config)

        start, end = parse_date_range(
            args.start,
            args.end,
        )

        stock_id = args.stock.strip()
        if not stock_id:
            raise ValueError("股票代號不可為空白")

        download_settings = settings["download"]
        timeout_seconds = download_settings["timeout_seconds"]
        request_interval = download_settings[
            "request_interval_seconds"
        ]
        db_path = settings["storage"]["database_path"]

        all_records = []

        for index, month in enumerate(month_starts(start, end)):
            if index > 0:
                time.sleep(request_interval)

            print(
                f"下載中：{stock_id} / {month:%Y-%m}",
                flush=True,
            )

            result = fetch_month_with_retry(
                stock_id,
                month.strftime("%Y%m%d"),
                timeout_seconds=timeout_seconds,
                max_attempts=download_settings["max_attempts"],
                retry_wait_seconds=download_settings[
                    "retry_wait_seconds"
                ],
            )

            monthly_records = [
                normalize_row(
                    stock_id,
                    result["fields"],
                    row,
                )
                for row in result["data"]
            ]

            all_records.extend(monthly_records)

        # 月份資料下載完成後，只保留指定日期區間。
        records = filter_records(
            all_records,
            start,
            end,
        )
        records.sort(key=lambda record: record["date"])

        if not records:
            print("\n指定期間內沒有可儲存的行情。")
            return 0

        # 全部月份處理成功後，才寫入資料庫。
        processed_count = save_records(
            records,
            db_path=db_path,
        )

    except (
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        sqlite3.Error,
        OSError,
    ) as error:
        print(f"資料處理失敗：{error}")
        return 1

    print(f"\n股票代號：{stock_id}")
    print(f"指定期間：{start} ～ {end}")
    print(f"整理後筆數：{len(records)}")
    print(f"資料庫本次處理筆數：{processed_count}")
    print(f"資料庫位置：{db_path}")
    print(
        f"實際資料期間：{records[0]['date']}"
        f" ～ {records[-1]['date']}"
    )

    if args.show:
        print("\n全部整理後資料：")

        for record in records:
            print(record)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())