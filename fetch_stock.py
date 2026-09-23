import argparse
import json
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database import DEFAULT_DB_PATH, save_records
from date_range import (
    parse_date_range,
    month_starts,
    filter_records,
)
from stock_data import normalize_row


def fetch_month(stock_id, month):
    """下載指定股票的一個月份行情。"""
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

    with urlopen(request, timeout=30) as response:
        result = json.load(response)

    if not isinstance(result, dict):
        raise ValueError("資料來源回應不是預期的字典格式")

    if result.get("stat") != "OK":
        raise ValueError(f"行情查詢未成功：{result.get('stat')}")

    if not result.get("fields") or not result.get("data"):
        raise ValueError("回應缺少欄位或行情資料")

    return result


def parse_arguments():
    """讀取終端機傳入的參數。"""
    parser = argparse.ArgumentParser(
        description="下載指定日期區間的台股上市股票日行情"
    )

    parser.add_argument(
        "--stock",
        required=True,
        help="股票代號，例如 2330",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="開始日期，例如 2026-08-15",
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
        start, end = parse_date_range(args.start, args.end)

        records = []

        for index, month in enumerate(month_starts(start, end)):
            # 月份之間暫停，避免連續密集請求。
            if index > 0:
                time.sleep(3)

            month_text = month.strftime("%Y%m%d")
            print(f"下載中：{args.stock} / {month:%Y-%m}")

            result = fetch_month(args.stock, month_text)

            monthly_records = [
                normalize_row(args.stock, result["fields"], row)
                for row in result["data"]
            ]

            records.extend(monthly_records)

        # 月資料合併後，只保留使用者指定的日期區間。
        records = filter_records(records, start, end)
        records.sort(key=lambda record: record["date"])

        if not records:
            print("指定日期區間內沒有行情，本次未寫入資料庫。")
            return 0

        # 所有月份均下載、整理成功後，才寫入資料庫。
        processed_count = save_records(records)

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

    print("\n股票代號：", args.stock)
    print("指定期間：", start, "～", end)
    print("整理後筆數：", len(records))
    print("資料庫本次處理筆數：", processed_count)
    print("資料庫位置：", DEFAULT_DB_PATH)
    print(
        "實際資料期間：",
        records[0]["date"],
        "～",
        records[-1]["date"],
    )

    if args.show:
        print("\n全部整理後資料：")
        for record in records:
            print(record)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())