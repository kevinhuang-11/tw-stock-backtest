"""把報表與工作整理成畫面用的摘要；只做呈現，不重新計算任何結果。"""
import re
from datetime import datetime, timezone

from tw_stock_backtest.web_format import fmt_percent, kind_label

# 報表清單的類型篩選；順序即畫面上的順序。
REPORT_KINDS = ("research", "backtest", "comparison", "period_comparison",
                "watchlist", "supplemental")


def created_at(path):
    """報表建立時間：目錄名稱帶有 UTC 時間戳；無法解析時退回檔案時間。"""
    name = path.name
    stamp = re.match(r"(\d{8})T(\d{6})_(\d{6})Z", name)
    if stamp:
        return datetime.strptime("".join(stamp.groups()), "%Y%m%d%H%M%S%f").replace(tzinfo=timezone.utc)
    stamp = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{6})\.(\d{6})_0000", name)
    if stamp:
        return datetime.strptime("".join(stamp.groups()), "%Y-%m-%d%H%M%S%f").replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return datetime.fromtimestamp(0, timezone.utc)


def strategy_name(snapshot):
    return ((snapshot or {}).get("definition") or {}).get("name")


def describe_report(key, entry):
    """回傳 {key, kind, kind_label, title, subtitle, status, created, metric}。"""
    path, details = entry["path"], entry["details"]
    kind = details.get("kind", "unknown")
    status = details.get("status", "incomplete")
    title, subtitle, metric = kind_label(kind), "", None

    if status in ("incomplete", "unreadable") and kind in ("research", "unknown"):
        subtitle = "尚未完成或無法讀取"
    elif kind == "research":
        name = strategy_name(details.get("strategy_snapshot"))
        title = "研究排名" + (" · " + name if name else "")
        counts = details.get("counts") or {}
        parts = ["分析日 " + (details.get("analysis_date") or "未提供")]
        if counts.get("ranked") is not None:
            parts.append(f"{counts['ranked']} 檔排名")
        if counts.get("excluded"):
            parts.append(f"排除 {counts['excluded']} 檔")
        subtitle = " · ".join(parts)
    elif kind == "backtest":
        configuration = details.get("configuration") or {}
        name = strategy_name(configuration.get("strategy_snapshot"))
        title = "回測" + (" · " + name if name else "")
        start, end = configuration.get("requested_start"), configuration.get("requested_end")
        subtitle = f"{start} ～ {end}" if start and end else ""
        if (path.parent / "comparison.json").is_file() or (path.parent.parent / "comparison.json").is_file():
            subtitle += " · 策略比較的子報表"
        total = (details.get("strategy") or {}).get("total_return")
        if total is not None:
            metric = {"label": "報酬率", "value": total, "text": fmt_percent(total, signed=True)}
    elif kind == "comparison":
        strategies = details.get("strategies") or []
        common = details.get("common") or {}
        title = f"策略比較 · {len(strategies)} 份策略"
        if common.get("start") and common.get("end"):
            subtitle = f"{common['start']} ～ {common['end']}"
    elif kind == "period_comparison":
        strategies = {row.get("id") for row in details.get("strategies") or []}
        periods = details.get("periods") or []
        title = f"跨期間比較 · {len(strategies)} 份策略"
        subtitle = f"{len(periods)} 個期間"
    elif kind == "watchlist":
        name = strategy_name(details.get("strategy_snapshot"))
        title = "自選快照" + (" · " + name if name else "")
        counts = details.get("counts") or {}
        subtitle = f"{counts.get('requested', 0)} 檔 · 通過 {counts.get('eligible', 0)} 檔"
    elif kind == "supplemental":
        stocks = (details.get("identity") or {}).get("stocks") or []
        title = "營收與法人更新"
        subtitle = "、".join(stocks[:5]) + (f" 等 {len(stocks)} 檔" if len(stocks) > 5 else "")

    return {"key": key, "kind": kind, "kind_label": kind_label(kind), "title": title,
            "subtitle": subtitle, "status": status, "created": created_at(path).isoformat(),
            "metric": metric, "folder": path.name}


def report_list(catalog, kind=None):
    """最新的在前；kind 有值時只留該類型。"""
    rows = [describe_report(key, entry) for key, entry in catalog.items()]
    if kind:
        rows = [row for row in rows if row["kind"] == kind]
    return sorted(rows, key=lambda row: (row["created"], row["key"]), reverse=True)


def kind_counts(catalog):
    counts = {}
    for entry in catalog.values():
        kind = entry["details"].get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts
