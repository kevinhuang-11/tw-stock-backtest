"""本機資料的新鮮度：行情、名單、營收與法人各自更新到哪一天。只讀，不連網。

專案沒有獨立的交易日曆，「落後幾個行情日」是以本機行情庫中出現過的日期推算：
某檔的最新日之後，行情庫裡還有幾個其他股票有行情的日期。
"""
import json
import sqlite3
from bisect import bisect_right
from contextlib import closing
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


def _connect(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True))


@lru_cache(maxsize=8)
def _price_summary(path, modified):
    """依檔案修改時間快取；行情庫更新後自動重算。"""
    with _connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='stock_prices'").fetchone():
            return (), {}
        dates = tuple(row[0] for row in db.execute(
            "SELECT DISTINCT date FROM stock_prices ORDER BY date"))
        latest = dict(db.execute(
            "SELECT stock_id, MAX(date) FROM stock_prices GROUP BY stock_id"))
    return dates, latest


def price_summary(db_path):
    """回傳 (所有行情日, {股票: 最新日})；行情庫不存在時為空。"""
    path = Path(db_path)
    if not path.is_file():
        return (), {}
    return _price_summary(str(path.resolve()), path.stat().st_mtime_ns)


def market_lag(day, dates):
    """day 之後，本機行情庫還有幾個行情日。day 為空時回傳 None。"""
    if not day or not dates:
        return None
    return len(dates) - bisect_right(dates, day)


def _supplemental(path):
    path = Path(path)
    if not path.is_file():
        return {}
    with _connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='supplemental'").fetchone():
            return {}
        return {kind: {"latest": latest, "stocks": stocks} for kind, latest, stocks in db.execute(
            "SELECT kind, MAX(period), COUNT(DISTINCT stock_id) FROM supplemental GROUP BY kind")}


def _listing(path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        return {"downloaded_at": snapshot.get("downloaded_at"), "count": snapshot.get("count"),
                "synthetic": snapshot.get("scope") == "synthetic_example"}
    except (OSError, ValueError):
        return None


def data_status(db_path, today=None):
    """總覽用的資料狀態摘要。"""
    today = today or datetime.now(ZoneInfo("Asia/Taipei")).date()
    dates, latest = price_summary(db_path)
    newest = dates[-1] if dates else None
    behind = sorted(day for day in latest.values() if day != newest)
    db_path = Path(db_path)
    return {
        "price_latest": newest,
        "calendar_days": (today - date.fromisoformat(newest)).days if newest else None,
        "stocks": len(latest),
        "behind": len(behind),
        "behind_oldest": behind[0] if behind else None,
        "listing": _listing(db_path.with_name("listed_stocks.json")),
        "supplemental": _supplemental(db_path.with_name("supplemental.sqlite3")),
    }
