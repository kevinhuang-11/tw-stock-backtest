"""處置股與全額交割（變更交易方法）的期間：只偵測並標示，不模擬交易限制。

規則見 docs/HALT-POLICY.md 3.17、3.18。處置期間與全額交割期間都仍可交易，回測照常以
開盤價成交；報表在相關成交、期末持股與研究排名旁標示。

資料來源（證交所）：
- 處置：公布處置有價證券（announcement/punish），一次查詢一段期間，含公布日與處置起迄日。
- 全額交割：變更交易（fullDelivery/TWT85U）是某一天的名單；逐日下載後，連續出現在名單的
  行情日合併成一段期間。沒有下載過的日子不能當成「不在名單上」（見 coverage）。

處置期間若遇停止買賣、全日暫停交易會順延，這裡只保存公告的起迄日，不推算順延。
"""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from tw_stock_backtest.data.corporate_actions import records, roc_date
from tw_stock_backtest.data.database import migrate_database

DISPOSITION = "disposition"
FULL_DELIVERY = "full_delivery"
KINDS = (DISPOSITION, FULL_DELIVERY)
STOCK = re.compile(r"\d{4}")


def _field(row, name):
    value = row.get(name)
    if value is None:
        raise ValueError(f"缺少欄位：{name}")
    return str(value).strip()


def parse_dispositions(payload, *, source):
    """公布處置有價證券的回應轉成處置期間；只保留四位數代號（股票，不含權證等）。"""
    result = []
    for row in records(payload):
        stock_id = _field(row, "證券代號")
        if not STOCK.fullmatch(stock_id):
            continue
        period = re.split(r"[～~]", _field(row, "處置起迄時間"))
        if len(period) != 2:
            raise ValueError(f"{stock_id} 的處置起迄時間無法辨識：{row.get('處置起迄時間')!r}")
        start, end = (roc_date(part) for part in period)
        if end < start:
            raise ValueError(f"{stock_id} 的處置迄日早於起日")
        result.append(
            {
                "stock_id": stock_id,
                "kind": DISPOSITION,
                "start_date": start,
                "end_date": end,
                "announced": roc_date(_field(row, "公布日期")),
                "measure": _field(row, "處置措施"),
                "detail": {
                    "name": _field(row, "證券名稱"),
                    "condition": row.get("處置條件"),
                    "content": row.get("處置內容"),
                },
                "source": source,
            }
        )
    return result


def parse_full_delivery(payload, day, *, source):
    """某一天的變更交易名單；分盤集合競價（以 ** 表示）另外記在 measure。"""
    if payload.get("date") and payload["date"] != day.replace("-", ""):
        raise ValueError(f"變更交易名單日期不符：要求 {day}，回應 {payload['date']}")
    result = []
    for row in records(payload):
        stock_id = _field(row, "證券代號")
        if not STOCK.fullmatch(stock_id):
            continue
        split = "**" in str(row.get("分盤集合競價(以**表示)", ""))
        result.append(
            {
                "stock_id": stock_id,
                "kind": FULL_DELIVERY,
                "start_date": day,
                "end_date": day,
                "announced": None,
                "measure": "變更交易方法（全額交割）" + ("，分盤集合競價" if split else ""),
                "detail": {"name": _field(row, "證券名稱")},
                "source": source,
            }
        )
    return result


def save_restrictions(entries, db_path, *, fetched_at, coverage):
    """寫入期間與已下載的範圍（同一個交易）；寫入前先升級 schema。

    coverage：[(種類, 開始日, 結束日)]。全額交割逐日下載，每天一筆。
    """
    for entry in entries:
        if entry["kind"] not in KINDS:
            raise ValueError(f"未知的交易限制類型：{entry['kind']}")
        if not entry["source"].startswith("https://"):
            raise ValueError("交易限制來源必須是 https 網址")
    migrate_database(db_path)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.executemany(
            "INSERT INTO trading_restrictions (stock_id, kind, start_date, end_date, announced, "
            "measure, detail, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(stock_id, kind, start_date) DO UPDATE SET end_date = excluded.end_date, "
            "announced = excluded.announced, measure = excluded.measure, "
            "detail = excluded.detail, source = excluded.source, fetched_at = excluded.fetched_at",
            [
                (
                    e["stock_id"],
                    e["kind"],
                    e["start_date"],
                    e["end_date"],
                    e["announced"],
                    e["measure"],
                    json.dumps(e["detail"], ensure_ascii=False, sort_keys=True),
                    e["source"],
                    fetched_at,
                )
                for e in entries
            ],
        )
        connection.executemany(
            "INSERT INTO trading_restriction_coverage (kind, start_date, end_date, fetched_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(kind, start_date, end_date) "
            "DO UPDATE SET fetched_at = excluded.fetched_at",
            [(kind, start, end, fetched_at) for kind, start, end in coverage],
        )
    return len(entries)


def _has_tables(connection):
    return (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('trading_restrictions', 'trading_restriction_coverage')"
        ).fetchone()[0]
        == 2
    )


def load_restrictions(db_path):
    """{股票: [{kind, start_date, end_date, announced, measure, label, source}]}，依起日排序。

    全額交割的每日名單在這裡合併：同一檔在連續下載過的日子都在名單上，就併成一段。
    資料庫或表不存在時為空字典。
    """
    path = Path(db_path).resolve()
    if not path.is_file():
        return {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if not _has_tables(connection):
            return {}
        rows = connection.execute(
            "SELECT stock_id, kind, start_date, end_date, announced, measure, source "
            "FROM trading_restrictions ORDER BY stock_id, kind, start_date"
        ).fetchall()
        snapshot_days = [
            day
            for (day,) in connection.execute(
                "SELECT start_date FROM trading_restriction_coverage WHERE kind = ? "
                "ORDER BY start_date",
                (FULL_DELIVERY,),
            )
        ]
    position = {day: index for index, day in enumerate(snapshot_days)}
    result = {}
    for stock_id, kind, start, end, announced, measure, source in rows:
        items = result.setdefault(stock_id, [])
        last = items[-1] if items else None
        if (
            kind == FULL_DELIVERY
            and last
            and last["kind"] == FULL_DELIVERY
            and last["measure"] == measure
            and start in position
            and last["end_date"] in position
            and position[start] == position[last["end_date"]] + 1
        ):
            last["end_date"] = end
            continue
        items.append(
            {
                "kind": kind,
                "start_date": start,
                "end_date": end,
                "announced": announced,
                "measure": measure,
                "source": source,
            }
        )
    for items in result.values():
        for item in items:
            item["label"] = label(item)
        items.sort(key=lambda item: (item["start_date"], item["kind"]))
    return result


def label(item):
    if item["kind"] == DISPOSITION:
        return f"處置（{item['measure']}，{item['start_date']} ～ {item['end_date']}）"
    return f"{item['measure']}（{item['start_date']} ～ {item['end_date']}）"


def active(restrictions, stock_id, day):
    """day 當天生效中的限制說明（處置要已公布）；沒有時為空清單。"""
    return [
        item["label"]
        for item in restrictions.get(stock_id, [])
        if item["start_date"] <= day <= item["end_date"]
        and (item["announced"] is None or item["announced"] <= day)
    ]


def annotate_backtest(result, restrictions):
    """在成交與期末持股加上當天生效中的限制（只標示，不改變任何數字）。"""
    for trade in result.get("trades", []):
        labels = active(restrictions, trade["stock_id"], trade["date"])
        if labels:
            trade["restrictions"] = labels
    last = result["equity_curve"][-1]["date"] if result.get("equity_curve") else None
    for position in result.get("final_positions", []):
        labels = active(restrictions, position["stock_id"], last) if last else []
        if labels:
            position["restrictions"] = labels
    return result


def coverage_summary(db_path):
    """已下載的範圍：{"disposition": [[起, 迄]], "full_delivery": {"first", "last", "days"}}；
    沒有下載過時為 None。報表用它說明標示的範圍，沒下載過的期間不能當成「沒有限制」。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if not _has_tables(connection):
            return None
        rows = connection.execute(
            "SELECT kind, start_date, end_date FROM trading_restriction_coverage "
            "ORDER BY kind, start_date"
        ).fetchall()
    if not rows:
        return None
    days = [start for kind, start, _ in rows if kind == FULL_DELIVERY]
    return {
        DISPOSITION: [[start, end] for kind, start, end in rows if kind == DISPOSITION],
        FULL_DELIVERY: ({"first": days[0], "last": days[-1], "days": len(days)} if days else None),
    }


def annotate_rankings(rows, restrictions, day):
    """研究排名的每一列加上分析日生效中的限制（只標示，不排除、不改變名次）。"""
    for row in rows:
        labels = active(restrictions, row["stock_id"], day)
        if labels:
            row["restrictions"] = labels
    return rows
