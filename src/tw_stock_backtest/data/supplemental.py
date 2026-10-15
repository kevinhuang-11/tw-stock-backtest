"""Official supplemental data, deliberately separate from strategy inputs.

Revenue endpoint is a current snapshot, not a historical API. Publication time
is unknown; fetched_at and source_generated_date must never stand in for it.
"""

import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
HISTORY_URL = "https://wwwc.twse.com.tw/rwd/zh/IIH/company/financial"
INSTITUTION_URL = "https://www.twse.com.tw/fund/T86"
GROUPS = {
    "foreign": "外陸資（不含外資自營商）",
    "foreign_dealer": "外資自營商（另列）",
    "trust": "投信",
    "dealer_own": "自營商自行買賣",
    "dealer_hedge": "自營商避險",
    "dealer": "自營商合計",
}


def listed_scope(settings, stocks):
    from tw_stock_backtest.data.universe import ensure_network_allowed, load_universe

    # 更新流程都先經過這裡；合成示例名單在發出任何請求前就拒絕。
    ensure_network_allowed(settings["storage"]["database_path"])
    path = Path(settings["storage"]["database_path"]).with_name("listed_stocks.json")
    if not path.is_file():
        raise ValueError(
            "缺少上市普通股名單；請先用 update_universe 建立 listed_stocks.json，不下載全市場行情"
        )
    snapshot = load_universe(path)
    by_id = {row["stock_id"]: row for row in snapshot["stocks"]}
    if any(sid not in by_id for sid in stocks):
        raise ValueError("選取股票不在目前上市普通股名單；請核對股票或更新名單")
    return {
        "source_url": snapshot["source_url"],
        "downloaded_at": snapshot["downloaded_at"],
        "stocks": [by_id[sid] for sid in sorted(stocks)],
    }


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def number(value, integer=False):
    if value is None or str(value).strip() in ("", "--", "-", "N/A", "不適用"):
        return None
    try:
        n = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("來源數字格式錯誤") from exc
    if not n.is_finite() or (integer and n != n.to_integral_value()):
        raise ValueError("來源數字非有限值或非整數")
    return str(n)


def validate_month_range(start, end):
    for value in (start, end):
        if value:
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
                raise ValueError("營收月份格式需為 YYYY-MM")
            date.fromisoformat(value + "-01")
    if bool(start) != bool(end) or start and start > end:
        raise ValueError("營收月份需同時指定起迄，且起月不得晚於迄月")
    if start and (int(end[:4]) - int(start[:4])) * 12 + int(end[5:]) - int(start[5:]) >= 24:
        raise ValueError("營收單次月份範圍限 24 個月")


def roc_month(value):
    s = str(value).strip().replace("/", "")
    if not re.fullmatch(r"\d{5}", s):
        raise ValueError("營收年月必須是民國 YYYMM")
    return date(int(s[:3]) + 1911, int(s[3:]), 1).isoformat()[:7]


def growth(current, base):
    if current is None or base is None:
        return {"value": None, "reason": "缺少本期或精確對應期資料"}
    if Decimal(base) <= 0:
        return {"value": None, "reason": "分母為零或負值，不適合計算"}
    return {"value": str(Decimal(current) / Decimal(base) - 1), "reason": None}


def parse_revenue(payload, stocks, fetched_at):
    if not isinstance(payload, list) or not payload:
        raise ValueError("月營收回應格式錯誤或空白，不能判定尚未公布")
    result = []
    for raw in payload:
        sid = str(raw.get("公司代號", "")).strip()
        if sid not in stocks:
            continue
        generated = raw.get("出表日期", "")
        if not re.fullmatch(r"\d{7}", generated):
            raise ValueError("出表日期格式錯誤")
        generated = date(
            int(generated[:3]) + 1911, int(generated[3:5]), int(generated[5:])
        ).isoformat()
        values = [
            number(raw[key])
            for key in ("營業收入-當月營收", "營業收入-上月營收", "營業收入-去年當月營收")
        ]
        result.append(
            {
                "stock_id": sid,
                "period": roc_month(raw["資料年月"]),
                "revenue": values[0],
                "previous_month": values[1],
                "previous_year": values[2],
                "mom": growth(values[0], values[1]),
                "yoy": growth(values[0], values[2]),
                "official_mom_percent": number(raw["營業收入-上月比較增減(%)"]),
                "official_yoy_percent": number(raw["營業收入-去年同月增減(%)"]),
                "unit": "TWD thousand",
                "basis": "t187ap05_L 原表口徑（不跨来源拼接）",
                "source": REVENUE_URL,
                "source_generated_date": generated,
                "published_at": None,
                "fetched_at": fetched_at,
                "raw": raw,
            }
        )
    return result


def parse_revenue_history(payload, stock, fetched_at):
    """IIH rolling monthly series, NTD converted exactly to thousand NTD.

    Its response does not identify consolidation or publication time. Keep it
    in revenue_history, never join its denominators to OpenAPI observations.
    Only exact calendar predecessors from this response form growth rates.
    """
    if payload.get("info", {}).get("data", {}).get("code") != stock:
        raise ValueError("歷史營收股票不符")
    chart = payload["chart"]["revenue"]
    series = [x for x in chart["series"] if x.get("name") == "月營收"]
    if len(series) != 1 or len(chart["categories"]) != len(series[0]["data"]):
        raise ValueError("歷史營收欄位不符")
    values = {}
    for period, value in zip(chart["categories"], series[0]["data"]):
        if not re.fullmatch(r"\d{6}", period):
            raise ValueError("西元年月格式錯誤")
        month = date(int(period[:4]), int(period[4:]), 1).isoformat()[:7]
        if month in values:
            raise ValueError("重複營收月份")
        n = number(value)
        values[month] = str(Decimal(n) / 1000) if n is not None else None
    rows = []
    for month, revenue in sorted(values.items()):
        d = date.fromisoformat(month + "-01")
        previous = (d - timedelta(days=1)).isoformat()[:7]
        year = f"{d.year - 1:04d}-{d.month:02d}"
        rows.append(
            {
                "stock_id": stock,
                "period": month,
                "revenue": revenue,
                "previous_month": values.get(previous),
                "previous_year": values.get(year),
                "mom": growth(revenue, values.get(previous)),
                "yoy": growth(revenue, values.get(year)),
                "official_mom_percent": None,
                "official_yoy_percent": None,
                "unit": "TWD thousand",
                "original_unit": "TWD",
                "basis": "IIH 月營收；來源未標示合併／個別，不跨來源拼接",
                "source": HISTORY_URL,
                "source_generated_date": None,
                "published_at": None,
                "fetched_at": fetched_at,
                "raw": {
                    "month": month,
                    "revenue_ntd": None if revenue is None else str(Decimal(revenue) * 1000),
                },
            }
        )
    return rows


def parse_institution(payload, day, stocks, fetched_at):
    if not isinstance(payload, dict):
        raise ValueError("法人回應不是物件")
    if payload.get("stat") in ("查無資料", "很抱歉，沒有符合條件的資料!"):
        return []
    if payload.get("stat") != "OK" or payload.get("date") != day.replace("-", ""):
        raise ValueError("法人回應狀態或日期不符")
    if "股" not in payload.get("hints", ""):
        raise ValueError("無法確認法人資料單位")
    rows = []
    for values in payload["data"]:
        if len(values) != len(payload["fields"]):
            raise ValueError("法人欄位長度不符")
        raw = dict(zip(payload["fields"], values))
        sid = raw["證券代號"].strip()
        if sid not in stocks:
            continue
        groups = {}
        for key, prefix, suffix in (
            ("foreign", "外陸資", "(不含外資自營商)"),
            ("foreign_dealer", "外資自營商", ""),
            ("trust", "投信", ""),
            ("dealer_own", "自營商", "(自行買賣)"),
            ("dealer_hedge", "自營商", "(避險)"),
        ):
            group = {
                k: number(raw[prefix + label + "股數" + suffix], True)
                for k, label in (("buy", "買進"), ("sell", "賣出"), ("net", "買賣超"))
            }
            if any(group[k] is not None and Decimal(group[k]) < 0 for k in ("buy", "sell")):
                raise ValueError("法人買進與賣出不得為負數")
            if all(v is not None for v in group.values()) and Decimal(group["buy"]) - Decimal(
                group["sell"]
            ) != Decimal(group["net"]):
                raise ValueError("法人買賣差額與官方淨額不一致")
            groups[key] = group

        def add(a, b):
            return str(Decimal(a) + Decimal(b)) if a is not None and b is not None else None

        groups["dealer"] = {
            k: add(groups["dealer_own"][k], groups["dealer_hedge"][k])
            for k in ("buy", "sell", "net")
        }
        if groups["dealer"]["net"] != number(raw["自營商買賣超股數"], True):
            raise ValueError("自營商分類合計不一致")
        total = number(raw["三大法人買賣超股數"], True)
        calculated = add(
            add(groups["foreign"]["net"], groups["trust"]["net"]), groups["dealer"]["net"]
        )
        if calculated is not None and total != calculated:
            raise ValueError("三大法人合計不一致；外資自營商不得重複計入")
        rows.append(
            {
                "stock_id": sid,
                "period": day,
                "groups": groups,
                "total": total,
                "unit": "shares",
                "source": INSTITUTION_URL,
                "published_at": None,
                "fetched_at": fetched_at,
                "raw": raw,
                "notes": payload.get("notes", []),
            }
        )
    return rows


class Store:
    """Separate DB; every response batch and revision audit commit together."""

    def __init__(self, path):
        self.path = Path(path)

    def save(self, kind, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS supplemental(kind TEXT, stock_id TEXT, period TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,stock_id,period))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, kind TEXT, stock_id TEXT, period TEXT, replaced_at TEXT, old_payload TEXT)"
            )
            seen = set()
            for row in rows:
                key = (kind, row["stock_id"], row["period"])
                if key in seen:
                    raise ValueError("回應含重複資料鍵")
                seen.add(key)
                old = db.execute(
                    "SELECT payload FROM supplemental WHERE kind=? AND stock_id=? AND period=?", key
                ).fetchone()
                if old and any(
                    json.loads(old[0]).get(k) != row.get(k) for k in ("source", "unit", "basis")
                ):
                    raise ValueError("來源、單位或口徑衝突；請分開保存，不可覆蓋")
                if old and json.loads(old[0])["raw"] != row["raw"]:
                    db.execute(
                        "INSERT INTO revisions(kind,stock_id,period,replaced_at,old_payload) VALUES(?,?,?,?,?)",
                        (*key, row["fetched_at"], old[0]),
                    )
                db.execute(
                    "INSERT INTO supplemental VALUES(?,?,?,?) ON CONFLICT(kind,stock_id,period) DO UPDATE SET payload=excluded.payload",
                    (*key, json.dumps(row, ensure_ascii=False)),
                )

    def read(self, kind, stock, cutoff="9999-12-31", limit=600):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='supplemental'").fetchone():
                return []
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM supplemental WHERE kind=? AND stock_id=? AND period<=? ORDER BY period DESC LIMIT ?",
                    (kind, stock, cutoff, limit),
                )
            ][::-1]

    def save_update_states(self, progress):
        """Keep each stock/kind's last attempt, so another update cannot erase it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS supplemental_updates(stock_id TEXT, kind TEXT, payload TEXT NOT NULL, PRIMARY KEY(stock_id,kind))"
            )
            for sid in progress["identity"]["stocks"]:
                for kind in progress["identity"]["kinds"]:
                    counts = {}
                    reasons = []
                    for key, item in progress["results"].items():
                        if not key.startswith(kind + ":") or sid not in item.get("stocks", {}):
                            continue
                        value = item.get("stocks", {}).get(sid, {}).get("status", item["status"])
                        if value == "success" and item["status"] == "partial":
                            value = "partial"
                        counts[value] = counts.get(value, 0) + 1
                        if value != "success":
                            reasons.append(
                                {
                                    "period": key.split(":", 1)[1],
                                    "status": value,
                                    "reason": item.get("reason")
                                    or item.get("stocks", {}).get(sid, {}).get("reason")
                                    or item.get("limitation")
                                    or "來源未提供完整資料，原因未知",
                                }
                            )
                    payload = {
                        "run_id": progress["run_id"],
                        "finished_at": progress["finished_at"],
                        "counts": counts,
                        "reasons": reasons,
                    }
                    db.execute(
                        "INSERT INTO supplemental_updates VALUES(?,?,?) ON CONFLICT(stock_id,kind) DO UPDATE SET payload=excluded.payload",
                        (sid, kind, json.dumps(payload, ensure_ascii=False)),
                    )

    def update_states(self, sid):
        if not self.path.exists():
            return {}
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='supplemental_updates'"
            ).fetchone():
                return {}
            return {
                kind: json.loads(payload)
                for kind, payload in db.execute(
                    "SELECT kind,payload FROM supplemental_updates WHERE stock_id=?", (sid,)
                )
            }


class Client:
    """Same timeout/retry policy as price downloader; fixed official URLs only."""

    def __init__(self, settings, sleep=time.sleep):
        self.settings, self.sleep, self.last = settings, sleep, None

    def get(self, kind, day=None):
        if kind == "revenue_history":
            if not re.fullmatch(r"[1-9][0-9]{3}", day or ""):
                raise ValueError("股票格式錯誤")
            url = HISTORY_URL + "?code=" + day
        elif kind == "revenue":
            url = REVENUE_URL
        elif kind == "institution":
            url = (
                INSTITUTION_URL
                + "?response=json&selectType=ALLBUT0999&date="
                + day.replace("-", "")
            )
        else:
            raise ValueError("未知來源")
        for attempt in range(self.settings["max_attempts"]):
            if self.last is not None:
                self.sleep(
                    max(
                        0,
                        float(self.settings["request_interval_seconds"])
                        - (time.monotonic() - self.last),
                    )
                )
            self.last = time.monotonic()
            try:
                with urlopen(
                    Request(url, headers={"User-Agent": "tw-stock-backtest/0.1"}),
                    timeout=self.settings["timeout_seconds"],
                ) as response:
                    body = response.read(8_000_001)
                    if len(body) > 8_000_000:
                        raise ValueError("來源回應過大")
                    return json.loads(body)
            except (URLError, TimeoutError, ConnectionError) as error:
                if isinstance(error, HTTPError) and error.code not in (502, 503, 504):
                    raise
                if attempt + 1 == self.settings["max_attempts"]:
                    raise
                self.sleep(self.settings["retry_wait_seconds"] * 2**attempt)


def cumulative(rows, price_days, cutoff, window):
    """Use explicit OHLC date baseline; missing dates never become zero."""
    expected = sorted(set(d for d in price_days if d <= cutoff))[-window:]
    available = {r["period"]: r for r in rows if r["period"] <= cutoff}
    present = [d for d in expected if d in available]
    missing = [d for d in expected if d not in available]
    complete = len(expected) == window and not missing
    totals = {}
    for group in ("foreign", "trust", "dealer"):
        values = [available[d]["groups"][group]["net"] for d in present]
        totals[group] = (
            str(sum(map(Decimal, values)))
            if complete and all(v is not None for v in values)
            else None
        )
    return {
        "window": window,
        "complete": complete and all(v is not None for v in totals.values()),
        "expected_dates": expected,
        "covered_dates": present,
        "missing_dates": missing,
        "count": len(present),
        "totals": totals,
        "limitation": "依本機 OHLC 日期檢查；非獨立交易日曆，無法證明市場日期完整性",
    }


def institution_plan(store, stocks, end, start=None, initial_days=35, refresh_days=7):
    if not 1 <= initial_days <= 90 or not 1 <= refresh_days <= 30:
        raise ValueError("初始化限 1～90 日；回抓限 1～30 日")
    if start is None:
        starts = []
        for sid in stocks:
            rows = store.read("institution", sid, end.isoformat(), 1)
            starts.append(
                date.fromisoformat(rows[-1]["period"]) - timedelta(days=refresh_days - 1)
                if rows
                else end - timedelta(days=initial_days - 1)
            )
        start = min(starts)
    if start > end or (end - start).days >= 90:
        raise ValueError("單次法人更新限 1～90 個日曆日，起日不得晚於迄日")
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
