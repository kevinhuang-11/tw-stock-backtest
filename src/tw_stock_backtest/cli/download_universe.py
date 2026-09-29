import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.data.universe import load_universe
from tw_stock_backtest.cli.update_universe import DEFAULT_UNIVERSE_PATH


DEFAULT_LOG_DIR = (
    DEFAULT_CONFIG_PATH.parent / "data" / "download_logs"
)


def download_batch(
    stocks,
    start_text,
    end_text,
    *,
    config_path,
    interval_seconds=3,
):
    """逐檔執行既有下載命令；個別失敗不終止整批。"""
    start, end = parse_date_range(start_text, end_text)
    results = []

    for index, stock in enumerate(stocks, start=1):
        stock_id = stock["stock_id"]
        name = stock["name"]

        # 避免查詢股票上市之前的月份。
        listed_date = stock["listed_date"]
        effective_start = max(start.isoformat(), listed_date)

        item = {
            "stock_id": stock_id,
            "name": name,
            "start": effective_start,
            "end": end.isoformat(),
        }

        print(
            f"\n[{index}/{len(stocks)}] {stock_id} {name}",
            flush=True,
        )

        if effective_start > end.isoformat():
            item.update(
                status="skipped",
                reason="股票在指定期間結束後才上市",
            )
            results.append(item)
            print(item["reason"], flush=True)
            continue

        command = [
            sys.executable,
            "-m",
            "tw_stock_backtest.cli.fetch_stock",
            "--config",
            str(Path(config_path).resolve()),
            "--stock",
            stock_id,
            "--start",
            effective_start,
            "--end",
            end.isoformat(),
        ]

        try:
            # 使用同一個虛擬環境中的 Python 執行。
            # 子程式的下載進度會直接顯示在終端機。
            completed = subprocess.run(
                command,
                check=False,
            )

        except OSError as error:
            item.update(
                status="failed",
                reason=f"無法啟動下載程式：{error}",
            )

        else:
            item["returncode"] = completed.returncode

            if completed.returncode == 0:
                item["status"] = "completed"
            else:
                item.update(
                    status="failed",
                    reason=(
                        "下載命令執行失敗，退出碼："
                        f"{completed.returncode}"
                    ),
                )

        results.append(item)

        if item["status"] == "failed":
            print(item["reason"], flush=True)

        # 原本程式已處理月份之間的間隔；
        # 這裡補上股票之間的間隔。
        if index < len(stocks):
            time.sleep(interval_seconds)

    return results


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="依上市股票名單批次下載行情"
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="行情下載使用的設定檔",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=DEFAULT_UNIVERSE_PATH,
        help="已保存的股票名單 JSON",
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="本次最多處理幾檔，預設 5",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="略過名單前幾檔，預設 0",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help="批次結果紀錄的儲存資料夾",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        if args.limit <= 0:
            raise ValueError("--limit 必須大於 0")

        if args.offset < 0:
            raise ValueError("--offset 不可小於 0")

        start, end = parse_date_range(args.start, args.end)
        settings = load_config(args.config)
        snapshot = load_universe(args.universe)

        # 固定按代號排序，讓 offset 的意義清楚。
        all_stocks = sorted(
            snapshot["stocks"],
            key=lambda stock: stock["stock_id"],
        )

        stocks = all_stocks[
            args.offset:args.offset + args.limit
        ]

        if not stocks:
            raise ValueError("指定範圍內沒有股票")

        # 先確認紀錄資料夾可以建立。
        args.log_dir.mkdir(parents=True, exist_ok=True)

    except (ValueError, OSError) as error:
        print(f"批次下載設定失敗：{error}")
        return 1

    started_at = datetime.now(timezone.utc)

    print(f"名單股票總數：{len(all_stocks)}")
    print(f"本次處理股票數：{len(stocks)}")
    print(f"指定期間：{start} ～ {end}")
    print(f"資料庫位置：{settings['storage']['database_path']}")
    print(
        "本次股票："
        + ", ".join(stock["stock_id"] for stock in stocks)
    )

    results = download_batch(
        stocks,
        start.isoformat(),
        end.isoformat(),
        config_path=args.config,
        interval_seconds=float(
            settings["download"]["request_interval_seconds"]
        ),
    )

    counts = {
        status: sum(
            item["status"] == status
            for item in results
        )
        for status in ("completed", "failed", "skipped")
    }

    report = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "universe_path": str(args.universe.resolve()),
        "universe_downloaded_at": snapshot["downloaded_at"],
        "config_path": str(args.config.resolve()),
        "database_path": str(settings["storage"]["database_path"]),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "counts": counts,
        "results": results,
    }

    log_path = args.log_dir / (
        started_at.strftime("%Y%m%dT%H%M%S_%fZ") + ".json"
    )

    try:
        log_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print(f"下載流程已結束，但無法保存批次紀錄：{error}")
        return 1

    print("\n批次執行摘要：")
    print(f"命令成功：{counts['completed']}")
    print(f"命令失敗：{counts['failed']}")
    print(f"略過：{counts['skipped']}")
    print(f"紀錄位置：{log_path.resolve()}")

    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())