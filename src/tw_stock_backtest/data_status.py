"""本機資料的新鮮度：行情、名單、營收與法人各自更新到哪一天。只讀，不連網。

專案沒有獨立的交易日曆，「落後幾個行情日」是以本機行情庫中出現過的日期推算：
某檔的最新日之後，行情庫裡還有幾個其他股票有行情的日期。

計數、分組與日期比較都在 SQLite 中完成（GROUP BY、視窗函式），不把全部日期讀進 Python；
SQL 只處理日期字串、筆數與 NULL，不碰以字串保存的 Decimal 價格。
"""

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


def _connect(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True))


def _has_prices(db):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_prices'"
    ).fetchone()


# 只比較日期字串與計數，不在 SQL 中處理價格；價格是以字串保存的 Decimal。
OVERVIEW_SQL = """
    WITH latest AS (
        SELECT stock_id, MAX(date) AS last_date
        FROM stock_prices
        GROUP BY stock_id
    ),
    newest AS (
        SELECT MAX(date) AS day FROM stock_prices
    )
    SELECT
        newest.day,
        COUNT(latest.stock_id),
        COUNT(CASE WHEN latest.last_date < newest.day THEN 1 END),
        MIN(CASE WHEN latest.last_date < newest.day THEN latest.last_date END)
    FROM newest LEFT JOIN latest
"""


@lru_cache(maxsize=8)
def _price_overview(path, modified):
    """依檔案修改時間快取；行情庫更新後自動重算。"""
    with _connect(path) as db:
        if not _has_prices(db):
            return None, 0, 0, None
        return db.execute(OVERVIEW_SQL).fetchone()


def price_overview(db_path):
    """回傳 (最新行情日, 股票數, 停在較舊日期的股票數, 其中最舊的日期)；行情庫不存在時為空。"""
    path = Path(db_path)
    if not path.is_file():
        return None, 0, 0, None
    return _price_overview(str(path.resolve()), path.stat().st_mtime_ns)


def market_dates(db_path):
    """本機行情庫出現過的所有日期，由舊到新；有日期索引時不需臨時排序。"""
    path = Path(db_path)
    if not path.is_file():
        return ()
    with _connect(path) as db:
        if not _has_prices(db):
            return ()
        return tuple(
            row[0] for row in db.execute("SELECT DISTINCT date FROM stock_prices ORDER BY date")
        )


def market_lag(db_path, day):
    """day 之後，本機行情庫還有幾個行情日。day 為空或行情庫不存在時回傳 None。"""
    path = Path(db_path)
    if not day or not path.is_file():
        return None
    with _connect(path) as db:
        if not _has_prices(db):
            return None
        if not db.execute("SELECT EXISTS (SELECT 1 FROM stock_prices)").fetchone()[0]:
            return None
        # 有日期索引時是 SEARCH，只讀 day 之後的索引項。
        return db.execute(
            "SELECT COUNT(DISTINCT date) FROM stock_prices WHERE date > ?", (day,)
        ).fetchone()[0]


STOCK_QUALITY_SQL = """
    WITH days AS (
        -- 本機行情庫出現過的日期依序編號；某檔最新日之後的行情日數 = 總數 − 該日編號。
        SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS day_number, COUNT(*) OVER () AS total
        FROM (SELECT DISTINCT date FROM stock_prices)
    ),
    per_stock AS (
        SELECT
            stock_id,
            COUNT(*) AS row_count,
            MIN(date) AS first_date,
            MAX(date) AS last_date,
            SUM(close IS NULL) AS missing_close
        FROM stock_prices
        GROUP BY stock_id
    )
    SELECT s.stock_id, s.row_count, s.first_date, s.last_date, s.missing_close,
           d.total - d.day_number AS market_lag
    FROM per_stock AS s
    JOIN days AS d ON d.date = s.last_date
    ORDER BY s.stock_id
"""

GAPS_SQL = """
    WITH days AS (
        SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS day_number
        FROM (SELECT DISTINCT date FROM stock_prices)
    ),
    ordered AS (
        SELECT
            p.stock_id,
            p.date,
            d.day_number,
            LAG(p.date) OVER per_stock AS previous_date,
            LAG(d.day_number) OVER per_stock AS previous_number
        FROM stock_prices AS p
        JOIN days AS d ON d.date = p.date
        WHERE (:stock_id IS NULL OR p.stock_id = :stock_id)
        WINDOW per_stock AS (PARTITION BY p.stock_id ORDER BY p.date)
    )
    SELECT
        stock_id,
        previous_date,
        date,
        day_number - previous_number - 1 AS missing_market_days,
        CAST(julianday(date) - julianday(previous_date) AS INTEGER) AS calendar_days
    FROM ordered
    WHERE previous_date IS NOT NULL
      AND (day_number - previous_number > 1
           OR julianday(date) - julianday(previous_date) > :calendar_days)
    ORDER BY stock_id, previous_date
"""


def stock_quality(db_path):
    """各股的筆數、起訖日、收盤價為 NULL 的筆數，以及最新日之後還有幾個行情日。"""
    path = Path(db_path)
    if not path.is_file():
        return []
    with _connect(path) as db:
        if not _has_prices(db):
            return []
        columns = ("stock_id", "rows", "first_date", "last_date", "missing_close", "market_lag")
        return [dict(zip(columns, row, strict=True)) for row in db.execute(STOCK_QUALITY_SQL)]


def price_gaps(db_path, stock_id=None, calendar_days=20):
    """行情缺口：同一檔相鄰兩筆之間，有其他股票的行情日卻沒有這檔，或間隔超過 calendar_days 個日曆日。

    前者表示這檔少了行情列（可能是下載缺漏或停牌未入庫）；後者用來找出全庫都缺的長期空白，
    例如曾經整段沒有下載的月份。只看日期，不讀價格。
    """
    path = Path(db_path)
    if not path.is_file():
        return []
    with _connect(path) as db:
        if not _has_prices(db):
            return []
        columns = ("stock_id", "previous_date", "date", "missing_market_days", "calendar_days")
        return [
            dict(zip(columns, row, strict=True))
            for row in db.execute(GAPS_SQL, {"stock_id": stock_id, "calendar_days": calendar_days})
        ]


def _supplemental(path):
    path = Path(path)
    if not path.is_file():
        return {}
    with _connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='supplemental'").fetchone():
            return {}
        return {
            kind: {"latest": latest, "stocks": stocks}
            for kind, latest, stocks in db.execute(
                "SELECT kind, MAX(period), COUNT(DISTINCT stock_id) FROM supplemental GROUP BY kind"
            )
        }


def _listing(path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        return {
            "downloaded_at": snapshot.get("downloaded_at"),
            "count": snapshot.get("count"),
            "synthetic": snapshot.get("scope") == "synthetic_example",
        }
    except (OSError, ValueError):
        return None


def data_status(db_path, today=None):
    """總覽用的資料狀態摘要。"""
    today = today or datetime.now(ZoneInfo("Asia/Taipei")).date()
    newest, stocks, behind, behind_oldest = price_overview(db_path)
    db_path = Path(db_path)
    return {
        "price_latest": newest,
        "calendar_days": (today - date.fromisoformat(newest)).days if newest else None,
        "stocks": stocks,
        "behind": behind,
        "behind_oldest": behind_oldest,
        "listing": _listing(db_path.with_name("listed_stocks.json")),
        "supplemental": _supplemental(db_path.with_name("supplemental.sqlite3")),
    }
