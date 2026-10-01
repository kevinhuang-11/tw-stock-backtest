"""一次完成增量更新、共同日期評分與研究報表。"""
import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config, apply_overrides
from tw_stock_backtest.data.universe import load_universe
from tw_stock_backtest.reporting import ResearchReport
from tw_stock_backtest.research import (
    taipei_today, program_version, update_stocks, evaluate_research,
    build_update_plans, load_research_progress, job_identity,
)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, f"參數錯誤：{message}\n")


def parse_arguments(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--stocks", nargs="+")
    source.add_argument("--universe")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--as-of", help="明確分析日期，同時為下載截止日")
    parser.add_argument("--resume", help="接續原研究執行的 progress.json，其他參數須相同")
    parser.add_argument("--start", help="明確補抓起日，優先於增量規劃")
    parser.add_argument("--initial-days", type=int, default=90, help="無資料股票的有限初始化天數")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--top", type=int)
    parser.add_argument("--database", help="覆寫資料庫路徑，不修改 config")
    parser.add_argument("--output-dir", default="reports/research")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    report = None
    started = datetime.now(timezone.utc)
    try:
        today = taipei_today()
        cutoff = date.fromisoformat(args.as_of) if args.as_of else today
        if args.resume and not args.as_of:
            saved = json.loads(Path(args.resume).read_text(encoding="utf-8"))
            cutoff = date.fromisoformat(saved["job"]["cutoff"])
        if args.as_of:
            args.as_of = cutoff.isoformat()
        start = date.fromisoformat(args.start) if args.start else None
        if start:
            args.start = start.isoformat()
        if cutoff > today or (start and start > cutoff):
            raise ValueError("日期不可在未來或起日晚於截止日")
        if args.initial_days <= 0:
            raise ValueError("initial-days 必須大於 0")
        if args.skip_download and (args.start or args.resume):
            raise ValueError("skip-download 不可搭配 start 或 resume")
        if args.limit is not None and (not args.universe or args.limit <= 0):
            raise ValueError("limit 必須為正整數且搭配 universe")
        settings = apply_overrides(load_config(args.config), {
            "universe": {"stocks": args.stocks},
            "factors": {"top_n": args.top},
            "storage": {"database_path": Path(args.database).resolve() if args.database else None},
        })
        snapshot = load_universe(args.universe) if args.universe else None
        stocks = sorted(snapshot["stocks"], key=lambda s: s["stock_id"]) if snapshot else [
            {"stock_id": sid, "name": "", "industry": ""} for sid in settings["universe"]["stocks"]]
        stocks = [{k: s[k] for k in ("stock_id", "name", "industry", "listed_date") if k in s}
                  for s in stocks]
        if args.limit:
            stocks = stocks[:args.limit]
        if not stocks:
            raise ValueError("股票池不可為空")
        settings["universe"]["stocks"] = [s["stock_id"] for s in stocks]
        version = program_version()
        job = {"cutoff": cutoff.isoformat(), "requested_as_of": args.as_of,
               "start": args.start, "initial_days": args.initial_days,
               "stocks": stocks, "settings": settings,
               "allow_partial": args.allow_partial, "commit": version["commit"]}
        plans, previous = [], []
        if args.resume:
            plans, previous = load_research_progress(args.resume, job)
        elif not args.skip_download:
            plans = build_update_plans(stocks, settings, cutoff, start=start,
                                       initial_days=args.initial_days)
        report = ResearchReport(args.output_dir)
        report.write_json("settings.json", {
            "settings": settings, "parameters": vars(args), "version": version, "run_id": report.run_id,
            "started_at": started, "taipei_today": today,
        })
        # 名單採白名單欄位，避免將來源檔的任意額外欄位寫入 artifacts。
        report.write_json("universe.json", {
            "source": "universe_file" if snapshot else "stocks" if args.stocks else "config",
            "downloaded_at": snapshot["downloaded_at"] if snapshot else None,
            "stocks": [{k: s[k] for k in ("stock_id", "name", "industry", "listed_date") if k in s}
                       for s in stocks],
        })
        def progress(rows, status="running"):
            report.write_json("progress.json", {
                "schema_version": 1, "kind": "research", "run_id": report.run_id,
                "job": job, "job_id": job_identity(job), "plans": plans,
                "status": status, "downloads": rows,
            })
        progress(previous)
        downloads = [] if args.skip_download else update_stocks(
            stocks, settings, cutoff, start=start, initial_days=args.initial_days,
            on_progress=progress, plans=plans, previous_results=previous,
        )
        result = evaluate_research(
            stocks, settings, cutoff, as_of=args.as_of,
            allow_partial=args.allow_partial, downloads=downloads,
        )
        result.update(run_id=report.run_id, version=version, requested_as_of=args.as_of,
                      taipei_today=today, lag_from_today_days=(today-date.fromisoformat(result["analysis_date"])).days if result["analysis_date"] else None,
                      started_at=started, finished_at=datetime.now(timezone.utc),
                      downloads=downloads, skipped_download=args.skip_download)
        progress(downloads, "finished")
        ranked = result["rankings"]
        output = report.finish(result)
        print(f"分析日期：{result['analysis_date']}；截止日：{cutoff}；台北今日：{today}")
        print(f"與台北今日相差 {result['lag_from_today_days']} 個日曆日；未驗證交易日完整性。")
        print(f"狀態：{result['status']}；退出碼：{result['exit_code']}")
        print(f"評估統計：{result['counts']}")
        if result["analysis_date"] and result["analysis_date"] < today.isoformat():
            print("注意：使用歷史行情，並非今日分析。")
        for row in ranked[:settings["factors"]["top_n"]]:
            print(f"{row['rank']}. {row['stock_id']} {row['score']:.2f}")
        print(f"報表：{output}")
        return result["exit_code"]
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError) as error:
        # 不輸出任意設定內容或來源回應；進度留在 incomplete 目錄供診斷。
        print(f"研究流程失敗 ({type(error).__name__})")
        if report:
            try:
                report.write_json("failure.json", {
                    "status": "failed", "exit_code": 1, "report_complete": False,
                    "error_type": type(error).__name__, "run_id": report.run_id, "started_at": started,
                    "finished_at": datetime.now(timezone.utc),
                })
            except OSError:
                pass
            print(f"未完成報表：{report.path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
