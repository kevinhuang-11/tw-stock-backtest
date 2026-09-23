import argparse
import sqlite3

from database import load_records
from indicators import simple_moving_average


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="讀取股票行情並顯示短期、長期均線"
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
        "--short",
        type=int,
        default=5,
        help="短期均線期間，預設為 5",
    )
    parser.add_argument(
        "--long",
        type=int,
        default=10,
        help="長期均線期間，預設為 10",
    )

    return parser.parse_args()


def format_price(value):
    """只在顯示時保留兩位小數；缺失值顯示 --。"""
    if value is None:
        return "--"

    return f"{value:.2f}"


def main():
    args = parse_arguments()

    try:
        if not (0 < args.short < args.long):
            raise ValueError("均線期間必須符合：0 < 短期 < 長期")

        records = load_records(
            args.stock,
            args.start,
            args.end,
        )

        if not records:
            print("資料庫中沒有符合條件的行情，請先下載資料。")
            return 0

        # 從每筆行情取出收盤價。
        closes = [record["close"] for record in records]

        # 分別計算短期與長期均線。
        short_ma = simple_moving_average(closes, args.short)
        long_ma = simple_moving_average(closes, args.long)

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"分析失敗：{error}")
        return 1

    print(f"股票代號：{args.stock}")
    print(f"資料筆數：{len(records)}")
    print(
        f"實際期間：{records[0]['date']} "
        f"～ {records[-1]['date']}"
    )

    print(
        f"\n{'Date':<12}"
        f"{'Close':>12}"
        f"{f'SMA{args.short}':>12}"
        f"{f'SMA{args.long}':>12}"
    )

    for record, short_value, long_value in zip(
        records,
        short_ma,
        long_ma,
    ):
        print(
            f"{record['date']:<12}"
            f"{format_price(record['close']):>12}"
            f"{format_price(short_value):>12}"
            f"{format_price(long_value):>12}"
        )

    if len(records) < args.long:
        print("\n資料不足，尚無法形成完整的長期均線。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())