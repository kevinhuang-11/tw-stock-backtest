import sqlite3
from contextlib import closing
from pathlib import Path


# 資料庫位置：專案資料夾/data/stocks.db
DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent / "data" / "stocks.db"
)


def save_records(records, db_path=DEFAULT_DB_PATH):
    """建立資料表，並新增或更新行情。"""
    db_path = Path(db_path)

    # data 資料夾不存在時，自動建立。
    db_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []

    for record in records:
        # SQLite 不能直接寫入 Decimal，先轉成字串。
        # 缺失價格保留 None，寫入資料庫後會是 NULL。
        prices = [
            str(record[key]) if record[key] is not None else None
            for key in ("open", "high", "low", "close")
        ]

        rows.append((
            record["stock_id"],
            record["date"],
            *prices,
            record["volume"],
            record["turnover"],
            record["trade_count"],
        ))

    # 開啟資料庫；檔案不存在時會自動建立。
    # closing 會在結束時關閉連線。
    with closing(sqlite3.connect(db_path)) as connection:
        # 寫入成功時提交；失敗時回滾本次交易。
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS stock_prices (
                    stock_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open TEXT,
                    high TEXT,
                    low TEXT,
                    close TEXT,
                    volume INTEGER NOT NULL CHECK (volume >= 0),
                    turnover INTEGER NOT NULL CHECK (turnover >= 0),
                    trade_count INTEGER NOT NULL CHECK (trade_count >= 0),
                    PRIMARY KEY (stock_id, date)
                )
            """)

            connection.executemany("""
                INSERT INTO stock_prices (
                    stock_id, date, open, high, low, close,
                    volume, turnover, trade_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(stock_id, date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    turnover = excluded.turnover,
                    trade_count = excluded.trade_count
            """, rows)

    # 本次處理筆數，包含新增與更新。
    return len(rows)