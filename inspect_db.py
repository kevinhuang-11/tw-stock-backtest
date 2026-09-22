import sqlite3
from contextlib import closing

from database import DEFAULT_DB_PATH


def main():
    # 唯讀模式：若資料庫不存在就報錯，不會意外建立空資料庫。
    uri = DEFAULT_DB_PATH.as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True)) as connection:
        summary = connection.execute("""
            SELECT stock_id, COUNT(*), MIN(date), MAX(date)
            FROM stock_prices
            GROUP BY stock_id
            ORDER BY stock_id
        """).fetchall()

        print("各股票資料摘要：")
        for stock_id, count, first_date, last_date in summary:
            print(
                f"{stock_id}：{count} 筆，"
                f"{first_date} ～ {last_date}"
            )

        records = connection.execute("""
            SELECT date, close, volume, turnover, trade_count
            FROM stock_prices
            WHERE stock_id = ?
            ORDER BY date DESC
            LIMIT 5
        """, ("2330",)).fetchall()

        print("\n2330 最近 5 筆：")
        for record in records:
            print(record)


if __name__ == "__main__":
    main()