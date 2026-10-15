import argparse
import sqlite3
from contextlib import closing

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.data.market_events import CONFIRMED_HALTS
from tw_stock_backtest.data_status import market_dates, price_gaps, stock_quality


def parse_arguments():
    parser = argparse.ArgumentParser(description="查看資料庫中的股票摘要與最近行情")

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
    parser.add_argument(
        "--gaps",
        action="store_true",
        help="列出行情缺口：其他股票有行情、這檔卻沒有的日子，或超過 --gap-days 個日曆日的空白",
    )
    parser.add_argument(
        "--gap-days",
        type=int,
        default=20,
        help="日曆日間隔超過幾天也視為缺口，預設 20（涵蓋農曆年等長假）",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        settings = load_config(args.config)

        if args.limit <= 0:
            raise ValueError("顯示筆數必須大於 0")

        stock_id = (
            args.stock.strip() if args.stock is not None else settings["universe"]["stocks"][0]
        )

        if not stock_id:
            raise ValueError("股票代號不可為空白")

        db_path = settings["storage"]["database_path"]

        # 唯讀開啟；資料庫不存在時報錯，不建立空資料庫。
        database_uri = db_path.as_uri() + "?mode=ro"

        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
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

        if args.gap_days < 0:
            raise ValueError("--gap-days 不可為負數")

        # 筆數、起訖日、缺價與落後行情日數都由 SQL 分組與視窗函式計算。
        summaries = stock_quality(db_path)
        gaps = price_gaps(db_path, calendar_days=args.gap_days) if args.gaps else None
        days = market_dates(db_path) if gaps else ()

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"資料庫檢視失敗：{error}")
        return 1

    print(f"資料庫位置：{db_path}")

    print("\n各股票資料摘要：")

    if not summaries:
        print("資料庫中尚無行情資料。")
    else:
        for row in summaries:
            notes = []
            if row["missing_close"]:
                notes.append(f"收盤價缺失 {row['missing_close']} 筆")
            if row["market_lag"]:
                notes.append(f"落後 {row['market_lag']} 個行情日")
            print(
                f"{row['stock_id']}：{row['rows']} 筆，"
                f"{row['first_date']} ～ {row['last_date']}"
                + ("（" + "，".join(notes) + "）" if notes else "")
            )

    if gaps is not None:
        print(f"\n行情缺口：{len(gaps)} 處")

        for gap in gaps:
            reason = (
                f"其他股票有 {gap['missing_market_days']} 個行情日"
                if gap["missing_market_days"]
                else "全庫都沒有行情"
            )
            missing = [day for day in days if gap["previous_date"] < day < gap["date"]]
            # 缺漏的行情日全部屬於已確認停牌時註明；讀取行情時會補上停牌列。
            if missing and all((gap["stock_id"], day) in CONFIRMED_HALTS for day in missing):
                reason += "（已確認停牌）"
            print(
                f"{gap['stock_id']}：{gap['previous_date']} → {gap['date']}，"
                f"相隔 {gap['calendar_days']} 個日曆日，{reason}"
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

            print(f"{record_date} | {close_text} | {volume:,} | {turnover:,} | {trade_count:,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
