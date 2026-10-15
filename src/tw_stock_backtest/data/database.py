import sqlite3
from contextlib import closing
from pathlib import Path
from decimal import Decimal
from tw_stock_backtest.date_range import parse_date_range


# 資料庫位置：專案資料夾/data/stocks.db
DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "stocks.db"
)


TABLE = """
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
"""

# 主鍵 (stock_id, date) 已涵蓋「單一股票＋日期範圍」的查詢；
# 以下索引服務以日期為主的橫斷面查詢，以及找出缺價列。
INDEXES = (
    # 行情日清單、某日之後還有幾個行情日、某一天的所有股票。
    "CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON stock_prices(date)",
    # 部分索引只收錄收盤價為 NULL 的列（全庫約 0.2%），找缺價時不必掃全表。
    "CREATE INDEX IF NOT EXISTS idx_stock_prices_missing_close "
    "ON stock_prices(stock_id, date) WHERE close IS NULL",
)

# Schema 版本記在 PRAGMA user_version。每個版本只新增、不改寫既有資料，
# 敘述都可重複執行；舊版程式建立的資料庫 user_version 為 0。
MIGRATIONS = (
    (1, (TABLE,)),      # 行情表；v1 程式建立的資料庫已有此表，這一步不會改動它
    (2, INDEXES),       # 日期索引與缺價部分索引
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def schema_version(connection):
    return connection.execute("PRAGMA user_version").fetchone()[0]


def migrate(connection):
    """把資料庫升級到 SCHEMA_VERSION，回傳 (升級前版本, 升級後版本)。

    每個版本在自己的 IMMEDIATE 交易中執行，連同 user_version 一起提交；
    失敗時整個版本回滾。版本在取得寫入鎖之後才讀取，兩個程序同時升級時，
    後到的會看到已完成的版本而略過。連線必須是自動提交模式（isolation_level=None）。
    """
    if connection.isolation_level is not None:
        raise ValueError("遷移需要自動提交模式的連線")

    before = schema_version(connection)
    if before > SCHEMA_VERSION:
        raise ValueError(
            f"資料庫 schema 版本 {before} 比程式支援的 {SCHEMA_VERSION} 新，請更新程式"
        )

    for version, statements in MIGRATIONS:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if schema_version(connection) >= version:
                connection.execute("COMMIT")
                continue
            for statement in statements:
                connection.execute(statement)
            # PRAGMA 不支援參數綁定；version 是上面定義的整數常數，不來自外部輸入。
            connection.execute(f"PRAGMA user_version = {int(version)}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    return before, schema_version(connection)


def migrate_database(db_path=DEFAULT_DB_PATH):
    """開啟（必要時建立）資料庫並升級 schema，回傳 (升級前版本, 升級後版本)。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(db_path, isolation_level=None)) as connection:
        return migrate(connection)


def read_schema_version(db_path=DEFAULT_DB_PATH):
    """唯讀查詢 schema 版本；資料庫不存在時回傳 None，不會建立檔案。"""
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        return None

    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as connection:
        return schema_version(connection)


def save_records(records, db_path=DEFAULT_DB_PATH):
    """新增或更新行情；寫入前先把資料庫升級到目前的 schema 版本。"""
    db_path = Path(db_path)

    # 建立 data 資料夾與資料表，並套用尚未執行的遷移（例如舊資料庫補上索引）。
    migrate_database(db_path)

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


def load_records(stock_id, start_text, end_text, db_path=DEFAULT_DB_PATH):
    """讀取指定股票與日期區間的行情，依日期由舊到新排列。"""
    start, end = parse_date_range(start_text, end_text)

    db_path = Path(db_path).resolve()

    # 唯讀模式，避免找不到檔案時建立空資料庫。
    uri = db_path.as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True)) as connection:
        # 讓查詢結果可以透過欄位名稱取值。
        connection.row_factory = sqlite3.Row

        rows = connection.execute("""
            SELECT
                stock_id, date, open, high, low, close,
                volume, turnover, trade_count
            FROM stock_prices
            WHERE stock_id = ?
              AND date >= ?
              AND date <= ?
            ORDER BY date ASC
        """, (
            stock_id,
            start.isoformat(),
            end.isoformat(),
        )).fetchall()

    records = []

    for row in rows:
        record = dict(row)

        for key in ("open", "high", "low", "close"):
            if record[key] is not None:
                record[key] = Decimal(record[key])

        records.append(record)

    return records


def next_record_date(stock_id, after_text, db_path=DEFAULT_DB_PATH):
    """回傳該股在 after_text 之後的第一個行情日；沒有或資料庫不存在時回傳 None。

    用來區分「指定日期沒有行情（例如休市日）」與「行情還沒更新到那天」。
    """
    db_path = Path(db_path).resolve()

    if not db_path.is_file():
        return None

    uri = db_path.as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True)) as connection:
        row = connection.execute(
            "SELECT MIN(date) FROM stock_prices WHERE stock_id = ? AND date > ?",
            (stock_id, after_text),
        ).fetchone()

    return row[0]
