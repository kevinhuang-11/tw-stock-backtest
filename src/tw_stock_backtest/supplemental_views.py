"""Read-only display of currently stored supplementary versions, never signals."""

import json
import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

from flask import redirect, render_template, request

from tw_stock_backtest.data.supplemental import GROUPS, Store, cumulative
from tw_stock_backtest.data.universe import NetworkDisabled


def panel_context(wb, sid, cutoff, month_start=None, month_end=None):
    """Current stored versions only. Supplement failures never hide price data."""
    from tw_stock_backtest.data.supplemental import validate_month_range

    validate_month_range(month_start, month_end)
    market = Path(wb.settings["storage"]["database_path"])
    database = market.with_name("supplemental.sqlite3")
    result = {
        "sid": sid,
        "cutoff": cutoff,
        "month_start": month_start or "",
        "month_end": month_end or "",
        "revenue": [],
        "institutions": [],
        "sums": [],
        "groups": GROUPS,
        "status": None,
        "updates": {},
        "warnings": [],
        "revenue_source": "OpenAPI 目前快照",
        "source_conflicts": [],
    }
    for kind, key, limit, end in [
        ("revenue", "revenue", 24, cutoff[:7]),
        ("institution", "institutions", 90, cutoff),
    ]:
        try:
            rows = Store(database).read(kind, sid, end, limit)
            if kind == "revenue":
                historical = Store(database).read("revenue_history", sid, end, 24)
                if historical:
                    old = {r["period"]: r for r in rows}
                    result["source_conflicts"] = [
                        r["period"]
                        for r in historical
                        if r["period"] in old
                        and (None if r["revenue"] is None else Decimal(r["revenue"]))
                        != (
                            None
                            if old[r["period"]]["revenue"] is None
                            else Decimal(old[r["period"]]["revenue"])
                        )
                    ]
                    result["revenue_source"] = "IIH 滾動歷史（與 OpenAPI 分開保存，不混接分母）"
                    if result["source_conflicts"]:
                        result["warnings"].append(
                            "兩來源同月份金額不一致："
                            + ",".join(result["source_conflicts"])
                            + "；保留各來源，未覆蓋。"
                        )
                    rows = historical if month_start or month_end else historical[-12:]
                rows = [
                    r
                    for r in rows
                    if (not month_start or r["period"] >= month_start)
                    and (not month_end or r["period"] <= month_end)
                ]
            result[key] = rows
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
            result["warnings"].append(
                ("月營收" if kind == "revenue" else "法人") + "資料無法讀取，其他研究區塊仍可使用。"
            )
    try:
        days = []
        if market.exists():
            with closing(sqlite3.connect(market.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                days = [
                    r[0]
                    for r in db.execute(
                        "SELECT date FROM stock_prices WHERE stock_id=? AND date<=? ORDER BY date DESC LIMIT 20",
                        (sid, cutoff),
                    )
                ]
        result["sums"] = [cumulative(result["institutions"], days, cutoff, n) for n in (5, 20)]
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        result["warnings"].append("行情日期基準無法讀取，暫不提供累計。")
    try:
        state = database.with_suffix(".status.json")
        if state.exists():
            saved = json.loads(state.read_text(encoding="utf-8"))
            if sid in saved["identity"]["stocks"]:
                status = {k: saved[k] for k in ("status", "finished_at", "counts")}
                status["by_kind"] = {}
                for kind in ("revenue", "institution"):
                    items = [r for k, r in saved["results"].items() if k.startswith(kind + ":")]
                    counts = {}
                    for item in items:
                        value = item.get("stocks", {}).get(sid, {}).get("status", item["status"])
                        counts[value] = counts.get(value, 0) + 1
                    status["by_kind"][kind] = counts
                result["status"] = status
    except (OSError, ValueError, KeyError, TypeError):
        result["warnings"].append("更新狀態檔無法讀取；不影響已保存資料。")
    try:
        result["updates"] = Store(database).update_states(sid)
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        result["warnings"].append("分種類更新狀態無法讀取。")
    return result


def register(app, wb, csrf):
    from tw_stock_backtest.stock_research import Watchlist, stock_id, today

    market = Path(wb.settings["storage"]["database_path"])
    database = market.with_name("supplemental.sqlite3")

    @app.get("/stocks/<sid>/supplemental")
    def supplemental_page(sid):
        try:
            stock_id(sid)
            cutoff = date.fromisoformat(request.args.get("end") or today()).isoformat()
            if cutoff > today():
                raise ValueError("日期在未來")
            context = panel_context(
                wb,
                sid,
                cutoff,
                request.args.get("month_start") or None,
                request.args.get("month_end") or None,
            )
            template = (
                "supplemental_panel.html"
                if request.args.get("fragment") == "1"
                else "supplemental.html"
            )
            return render_template(template, supp=context, csrf=csrf)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            return "補充資料或查詢日期無法讀取；未下載或修改資料", 400

    @app.post("/supplemental/update")
    def supplemental_update():
        try:
            scope = request.form.get("scope", "stock")
            if scope == "watchlist":
                stocks = [
                    r["stock_id"]
                    for r in Watchlist(wb.state / "private" / "watchlist.sqlite3").items()
                ]
                if not stocks:
                    raise ValueError("自選清單是空的，沒有建立工作")
            elif scope == "stock":
                stocks = [stock_id(request.form.get("stock_id", ""))]
            else:
                raise ValueError("更新範圍無效")
            job = wb.submit(
                {
                    "kind": "supplemental",
                    "stocks": " ".join(stocks),
                    "start": request.form.get("start", ""),
                    "end": request.form.get("end", ""),
                    "supplemental_kind": request.form.get("supplemental_kind", "both"),
                    "revenue_start": request.form.get("revenue_start", ""),
                    "revenue_end": request.form.get("revenue_end", ""),
                    "revenue_source": request.form.get("revenue_source", "latest"),
                }
            )
            return redirect("/#job-" + job, 303)
        except NetworkDisabled as error:
            return {"errors": {"_form": str(error) + "；未建立工作。"}}, 400
        except (ValueError, OSError):
            return {
                "errors": {
                    "_form": "更新未建立：請檢查股票、日期；自選需 1～10 檔，日期範圍不超過 90 日"
                }
            }, 400
