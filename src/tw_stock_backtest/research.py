"""增量更新與每日研究；共用既有下載、排名及資料庫功能。"""
from collections import Counter
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

from tw_stock_backtest.cli.fetch_stock import NoMarketData, fetch_month_with_retry
from tw_stock_backtest.cli.screen_factors import collect_factor_rows
from tw_stock_backtest.analysis.factors import rank_factor_candidates
from tw_stock_backtest.data.database import load_records, save_records
from tw_stock_backtest.data.stock_data import normalize_row
from tw_stock_backtest.date_range import month_starts, filter_records
from tw_stock_backtest.reporting import _json_default


def taipei_today():
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def program_version():
    root = Path.cwd()
    try:
        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
                text=True, timeout=5,
            ).strip()
        return {"commit": git("rev-parse", "HEAD"),
                "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def plan_update(latest, end, *, start=None, initial_days=90):
    """明確起日優先；自動更新重抓 latest 月，空資料只抓有限期間。"""
    if initial_days <= 0:
        raise ValueError("initial_days 必須大於 0")
    lower = start or (
        min(date.fromisoformat(latest), end).replace(day=1)
        if latest else end - timedelta(days=initial_days - 1)
    )
    if lower > end:
        raise ValueError("開始日期不能晚於結束日期")
    return lower, list(month_starts(lower, end))


def read_histories(stocks, db_path, end):
    if not Path(db_path).exists():
        return {s["stock_id"]: [] for s in stocks}
    return {s["stock_id"]: load_records(
        s["stock_id"], "1900-01-01", end.isoformat(), db_path=db_path,
    ) for s in stocks}


def build_update_plans(stocks, settings, end, *, start=None, initial_days=90):
    histories = read_histories(stocks, settings["storage"]["database_path"], end)
    plans = []
    for stock in stocks:
        history = histories[stock["stock_id"]]
        latest = history[-1]["date"] if history else None
        lower, months = plan_update(latest, end, start=start, initial_days=initial_days)
        if stock.get("listed_date"):
            lower = max(lower, date.fromisoformat(stock["listed_date"]))
            months = list(month_starts(lower, end)) if lower <= end else []
        plans.append({"stock_id": stock["stock_id"], "previous_latest": latest,
                      "start": lower.isoformat(), "end": end.isoformat(),
                      "months": [m.isoformat() for m in months]})
    return plans


def job_identity(job):
    return hashlib.sha256(json.dumps(job, sort_keys=True, default=_json_default,
                                     ensure_ascii=False).encode()).hexdigest()


def load_research_progress(path, job):
    """研究進度與舊 download_universe 的進度格式分開，避免誤用。"""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(job, default=_json_default))
    if not isinstance(document, dict):
        raise ValueError("resume 格式無效")
    if (document.get("schema_version") != 1 or document.get("kind") != "research"
            or document.get("job") != expected
            or document.get("job_id") != job_identity(expected)):
        raise ValueError("resume 的日期、股票池、設定或資料庫與原工作不符")
    plans = document.get("plans")
    ids = [s["stock_id"] for s in job["stocks"]]
    if (not isinstance(plans, list) or any(not isinstance(p, dict) for p in plans)
            or [p.get("stock_id") for p in plans] != ids):
        raise ValueError("resume 的下載計畫無效")
    for plan in plans:
        lower, end = date.fromisoformat(plan["start"]), date.fromisoformat(plan["end"])
        months = [m.isoformat() for m in month_starts(lower, end)] if lower <= end else []
        if plan["end"] != job["cutoff"] or plan["months"] != months:
            raise ValueError("resume 的月份計畫不符")
    results = document.get("downloads")
    if not isinstance(results, list):
        raise ValueError("resume 缺少下載結果")
    seen = set()
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("resume 結果格式無效")
        sid = item.get("stock_id")
        if sid not in ids or sid in seen or item.get("status") not in (
                "success", "partial", "failed", "no_data", "running", "skipped"):
            raise ValueError("resume 的下載結果無效")
        seen.add(sid)
    return plans, results


def update_stocks(stocks, settings, end, *, start=None, initial_days=90,
                  on_progress=None, plans=None, previous_results=()):
    db = settings["storage"]["database_path"]
    plans = plans if plans is not None else build_update_plans(
        stocks, settings, end, start=start, initial_days=initial_days)
    previous = {r["stock_id"]: r for r in previous_results}
    results = []
    requested = False
    download = settings["download"]
    for plan in plans:
        stock_id = plan["stock_id"]
        latest = plan["previous_latest"]
        lower = date.fromisoformat(plan["start"])
        months = [date.fromisoformat(m) for m in plan["months"]]
        old = previous.get(stock_id, {})
        if old.get("status") == "success" or old.get("skip_reason") == "resumed_success":
            # 避免進度仍在，但資料庫已被移除，卻宣稱已完成。
            if not Path(db).exists() or not load_records(stock_id, plan["start"], plan["end"], db_path=db):
                raise ValueError("resume 已成功項目缺少資料庫行情")
            results.append({**old, "status": "skipped", "skip_reason": "resumed_success"})
            if on_progress:
                on_progress(results)
            continue
        item = {"stock_id": stock_id, "previous_latest": latest,
                "start": lower.isoformat(), "end": end.isoformat(),
                "months": [], "status": "running", "saved_rows": 0}
        results.append(item)
        if on_progress:
            on_progress(results)
        for month in months:
            if requested:
                time.sleep(download["request_interval_seconds"])
            requested = True
            entry = {"month": month.isoformat(), "status": "running", "rows": 0}
            item["months"].append(entry)
            try:
                payload = fetch_month_with_retry(
                    stock_id, month.strftime("%Y%m%d"),
                    **{key: download[key] for key in (
                        "timeout_seconds", "max_attempts", "retry_wait_seconds")},
                )
                records = [normalize_row(stock_id, payload["fields"], row)
                           for row in payload["data"]]
                if any(date.fromisoformat(r["date"]).replace(day=1) != month
                       for r in records):
                    raise ValueError("來源月份不符")
                records = filter_records(records, lower, end)
                if records:
                    entry["rows"] = save_records(records, db_path=db)
                    entry["status"] = "success"
                    item["saved_rows"] += entry["rows"]
                else:
                    entry.update(status="no_data", reason="指定範圍沒有行情")
            except NoMarketData:
                entry.update(status="no_data", reason="來源明確回覆無資料，不代表停牌")
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
                # 不保存任意 exception 字串，避免 HTTP 回應或 URL 帶入憑證。
                entry.update(status="failed", reason=f"下載、解析或儲存失敗 ({type(error).__name__})")
            if on_progress:
                on_progress(results)
            if entry["status"] == "failed":
                # 不讓較新月份推進 latest，掩蓋較早月份的下載失敗。
                break
        if not months:
            item["skip_reason"] = "listed_after_cutoff"
        statuses = [m["status"] for m in item["months"]]
        item["status"] = (
            "skipped" if not months else
            "failed" if "failed" in statuses else
            "no_data" if not item["saved_rows"] else
            "partial" if "no_data" in statuses else "success"
        )
        if on_progress:
            on_progress(results)
    return results


def choose_analysis_date(histories, cutoff):
    """股票池最新可用日期；每檔再做同日新鮮度檢查，不往回遷就。"""
    dates = [r["date"] for records in histories.values() for r in records
             if r["date"] <= cutoff.isoformat()]
    return max(dates) if dates else None


def evaluate_research(stocks, settings, cutoff, *, as_of=None,
                      allow_partial=False, downloads=()):
    histories = read_histories(stocks, settings["storage"]["database_path"], cutoff)
    target = as_of or choose_analysis_date(histories, cutoff)
    actual = {sid: {"first": rows[0]["date"] if rows else None,
                    "latest": rows[-1]["date"] if rows else None,
                    "rows": len(rows)} for sid, rows in histories.items()}
    failed_updates = {r["stock_id"]: r["status"] for r in downloads
                      if r["status"] != "success" and r.get("skip_reason") != "resumed_success"}
    rows, errors = [], []
    if target and Path(settings["storage"]["database_path"]).exists():
        rows, errors = collect_factor_rows(
            stocks, target, db_path=settings["storage"]["database_path"],
            factor_settings=settings["factors"],
        )
    else:
        errors = [{"stock_id": s["stock_id"], "reason": "沒有可用分析日期或行情"}
                  for s in stocks]
    errors_by_id = {e["stock_id"]: e for e in errors}
    for sid in failed_updates:
        errors_by_id[sid] = {"stock_id": sid, "reason": f"本次更新狀態 {failed_updates[sid]}，排除未確認完整的更新"}
    rows = [r for r in rows if r["stock_id"] not in errors_by_id]
    ranked = []
    if rows and (allow_partial or not errors_by_id):
        ranked = rank_factor_candidates(rows, weights={
            name: settings["factors"][name + "_weight"]
            for name in ("momentum", "trend", "volatility")
        })
    lag = (cutoff - date.fromisoformat(target)).days if target else None
    incomplete = bool(errors_by_id) or bool(failed_updates)
    # 成功僅表示指定基準日的流程完整；與今日差距另列，不推定交易日。
    partial = incomplete
    status = "failed" if not ranked else "partial" if partial else "success"
    evaluations = []
    by_id = {r["stock_id"]: r for r in rows}
    for stock in stocks:
        sid = stock["stock_id"]
        signal = next((r for r in histories[sid] if r["date"] == target), None)
        evaluations.append({"stock_id": sid, "actual": actual[sid],
                            "signal": {"date": signal["date"], "close": signal["close"]} if signal else None,
                            "status": "excluded" if sid in errors_by_id else "evaluated",
                            "reason": errors_by_id.get(sid, {}).get("reason"),
                            "factors": by_id.get(sid)})
    return {"ranking_mode": "factors", "analysis_date": target, "cutoff": cutoff.isoformat(),
            "date_rule": "explicit" if as_of else "latest_available_in_selected_universe",
            "lag_calendar_days": lag, "status": status,
            "exit_code": {"success": 0, "partial": 2, "failed": 1}[status],
            "ranking_population": [r["stock_id"] for r in ranked],
            "rankings": ranked, "evaluations": evaluations,
            "excluded": list(errors_by_id.values()),
            "counts": {"requested": len(stocks), "evaluated": len(rows),
                       "excluded": len(errors_by_id), "ranked": len(ranked),
                       "downloads": dict(Counter(r["status"] for r in downloads))}}
