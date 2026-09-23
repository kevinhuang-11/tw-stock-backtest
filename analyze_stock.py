import argparse
import sqlite3

from config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from database import load_records
from indicators import simple_moving_average
from strategies import moving_average_crossover


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="讀取股票行情，顯示均線與交叉訊號"
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
        default=None,
        help="短期均線期間；未指定時使用設定檔",
    )
    parser.add_argument(
        "--long",
        type=int,
        default=None,
        help="長期均線期間；未指定時使用設定檔",
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
        settings = load_config(args.config)

        settings = apply_overrides(
            settings,
            {
                "strategy": {
                    "short_window": args.short,
                    "long_window": args.long,
                },
            },
        )

        short_window = settings["strategy"]["short_window"]
        long_window = settings["strategy"]["long_window"]
        db_path = settings["storage"]["database_path"]

        stock_id = args.stock.strip()
        if not stock_id:
            raise ValueError("股票代號不可為空白")

        records = load_records(
            stock_id,
            args.start,
            args.end,
            db_path=db_path,
        )

        if not records:
            print("資料庫中沒有符合條件的行情，請先下載資料。")
            return 0

        closes = [record["close"] for record in records]

        short_ma = simple_moving_average(
            closes,
            short_window,
        )
        long_ma = simple_moving_average(
            closes,
            long_window,
        )

        signals = moving_average_crossover(
            short_ma,
            long_ma,
        )

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"分析失敗：{error}")
        return 1

    print(f"股票代號：{stock_id}")
    print(f"資料庫位置：{db_path}")
    print(f"均線期間：短期 {short_window}，長期 {long_window}")
    print(f"資料筆數：{len(records)}")
    print(
        f"實際期間：{records[0]['date']}"
        f" ～ {records[-1]['date']}"
    )

    print(
        f"\n{'Date':<12}"
        f"{'Close':>12}"
        f"{f'SMA{short_window}':>12}"
        f"{f'SMA{long_window}':>12}"
        f"{'Signal':>10}"
    )

    for record, short_value, long_value, signal in zip(
        records,
        short_ma,
        long_ma,
        signals,
    ):
        print(
            f"{record['date']:<12}"
            f"{format_price(record['close']):>12}"
            f"{format_price(short_value):>12}"
            f"{format_price(long_value):>12}"
            f"{signal:>10}"
        )

    if len(records) < long_window:
        print("\n資料不足，尚無法形成完整的長期均線。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())