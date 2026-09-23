import argparse
import sqlite3
from contextlib import closing

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="查看資料庫中的股票摘要與最近行情"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置，預設使用專案中的 config.toml",
    )
    parser.add_argument(
        "--stock",
        default=None,
        help="查看指定股票；未指定時使用設定檔股票池的第一檔",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="顯示最近幾筆行情，預設為 5",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        settings = load_config(args.config)

        if args.limit <= 0:
            raise ValueError("顯示筆數必須大於 0")

        stock_id = (
            args.stock.strip()
            if args.stock is not None
            else settings["universe"]["stocks"][0]
        )

        if not stock_id:
            raise ValueError("股票代號不可為空白")

        db_path = settings["storage"]["database_path"]

        # 唯讀開啟；資料庫不存在時報錯，不建立空資料庫。
        database_uri = db_path.as_uri() + "?mode=ro"

        with closing(
            sqlite3.connect(database_uri, uri=True)
        ) as connection:
            summaries = connection.execute(
                """
                SELECT
                    stock_id,
                    COUNT(*),
                    MIN(date),
                    MAX(date)
                FROM stock_prices
                GROUP BY stock_id
                ORDER BY stock_id
                """
            ).fetchall()

            recent_records = connection.execute(
                """
                SELECT
                    date,
                    close,
                    volume,
                    turnover,
                    trade_count
                FROM stock_prices
                WHERE stock_id = ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (stock_id, args.limit),
            ).fetchall()

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"資料庫檢視失敗：{error}")
        return 1

    print(f"資料庫位置：{db_path}")

    print("\n各股票資料摘要：")

    if not summaries:
        print("資料庫中尚無行情資料。")
    else:
        for saved_stock_id, count, start, end in summaries:
            print(
                f"{saved_stock_id}：{count} 筆，"
                f"{start} ～ {end}"
            )

    print(f"\n{stock_id} 最近 {args.limit} 筆：")

    if not recent_records:
        print("沒有這檔股票的行情資料。")
    else:
        print("日期 | 收盤價 | 成交股數 | 成交金額 | 成交筆數")

        for record in recent_records:
            (
                record_date,
                close,
                volume,
                turnover,
                trade_count,
            ) = record

            close_text = "--" if close is None else close

            print(
                f"{record_date} | {close_text}"
                f" | {volume:,}"
                f" | {turnover:,}"
                f" | {trade_count:,}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())