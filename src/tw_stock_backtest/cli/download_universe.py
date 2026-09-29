import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import os
from tempfile import NamedTemporaryFile
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
    on_result=None,
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

            if on_result is not None:
                on_result(item)

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

        if on_result is not None:
            on_result(item)

        if item["status"] == "failed":
            print(item["reason"], flush=True)

        # 原本程式已處理月份之間的間隔；
        # 這裡補上股票之間的間隔。
        if index < len(stocks):
            time.sleep(interval_seconds)

    return results

def save_progress(report, path):
    """完整寫入暫存檔後，才替換既有進度檔。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)

            json.dump(
                report,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_progress(path, expected_job):
    """確認進度檔屬於相同工作，並檢查已保存的結果。"""
    with Path(path).open(encoding="utf-8") as file:
        report = json.load(file)

    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
    ):
        raise ValueError("不支援的進度檔格式")

    if report.get("job") != expected_job:
        raise ValueError(
            "進度檔的日期、股票範圍或設定與本次不同，"
            "請使用原本參數接續"
        )

    results = report.get("results")

    if not isinstance(results, list):
        raise ValueError("進度檔缺少結果清單")

    allowed_ids = {
        stock["stock_id"]
        for stock in expected_job["stocks"]
    }
    seen = set()

    for item in results:
        if not isinstance(item, dict):
            raise ValueError("進度檔包含無效結果")

        stock_id = item.get("stock_id")

        if (
            not isinstance(stock_id, str)
            or stock_id not in allowed_ids
            or stock_id in seen
        ):
            raise ValueError("進度檔包含未知或重複的股票")

        if item.get("status") not in (
            "completed",
            "failed",
            "skipped",
        ):
            raise ValueError("進度檔包含未知狀態")

        seen.add(stock_id)

    return report


def select_pending_stocks(stocks, results):
    """成功與確定略過的不再執行；失敗或未執行的繼續處理。"""
    finished_ids = {
        item["stock_id"]
        for item in results
        if item["status"] in ("completed", "skipped")
    }

    return [
        stock
        for stock in stocks
        if stock["stock_id"] not in finished_ids
    ]

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
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="從指定進度 JSON 接續，須搭配原本的下載參數",
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

        all_stocks = sorted(
            snapshot["stocks"],
            key=lambda stock: stock["stock_id"],
        )

        stocks = all_stocks[
            args.offset:args.offset + args.limit
        ]

        if not stocks:
            raise ValueError("指定範圍內沒有股票")

        # 把 Decimal、Path 等設定轉成可保存的 JSON 資料。
        settings_snapshot = json.loads(
            json.dumps(settings, default=str)
        )

        job = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "stocks": stocks,
            "config_path": str(args.config.resolve()),
            "settings": settings_snapshot,
        }

        now = datetime.now(timezone.utc)

        if args.resume is not None:
            log_path = args.resume.resolve()
            report = load_progress(log_path, job)

        else:
            log_path = (
                args.log_dir
                / (now.strftime("%Y%m%dT%H%M%S_%fZ") + ".json")
            ).resolve()

            report = {
                "schema_version": 1,
                "started_at": now.isoformat(),
                "universe_path": str(args.universe.resolve()),
                "universe_downloaded_at": snapshot["downloaded_at"],
                "job": job,
                "results": [],
            }

        pending = select_pending_stocks(
            stocks,
            report["results"],
        )

        # 先保存工作資訊，即使第一檔就中斷，也有可接續的檔案。
        report["status"] = "running"
        report["updated_at"] = now.isoformat()
        report.pop("finished_at", None)

        save_progress(report, log_path)

    except (ValueError, OSError) as error:
        print(f"批次下載設定失敗：{error}")
        return 1

    print(f"名單股票總數：{len(all_stocks)}")
    print(f"本次工作股票數：{len(stocks)}")
    print(f"已完成或略過：{len(stocks) - len(pending)}")
    print(f"待執行或重試：{len(pending)}")
    print(f"指定期間：{start} ～ {end}")
    print(f"資料庫位置：{settings['storage']['database_path']}")
    print(f"進度檔：{log_path}", flush=True)

    results_by_id = {
        item["stock_id"]: item
        for item in report["results"]
    }

    def record_result(item):
        # 重試結果會替換該股票上一次的失敗結果。
        results_by_id[item["stock_id"]] = item

        report["results"] = [
            results_by_id[stock["stock_id"]]
            for stock in stocks
            if stock["stock_id"] in results_by_id
        ]
        report["updated_at"] = datetime.now(timezone.utc).isoformat()

        save_progress(report, log_path)

    try:
        download_batch(
            pending,
            start.isoformat(),
            end.isoformat(),
            config_path=args.config,
            interval_seconds=float(
                settings["download"]["request_interval_seconds"]
            ),
            on_result=record_result,
        )

        counts = {
            status: sum(
                item["status"] == status
                for item in report["results"]
            )
            for status in ("completed", "failed", "skipped")
        }

        report["counts"] = counts
        report["status"] = (
            "finished_with_errors"
            if counts["failed"]
            else "finished"
        )
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["updated_at"] = report["finished_at"]

        save_progress(report, log_path)

    except KeyboardInterrupt:
        print("\n下載已中斷；已保存的進度可用 --resume 接續。")
        print(f"進度檔：{log_path}")
        return 130

    except (ValueError, OSError) as error:
        print(f"\n批次處理停止：{error}")
        print("接續時，以進度檔中最後成功保存的內容為準。")
        print(f"進度檔：{log_path}")
        return 1

    print("\n批次執行摘要：")
    print(f"命令成功：{counts['completed']}")
    print(f"命令失敗：{counts['failed']}")
    print(f"略過：{counts['skipped']}")
    print(f"進度檔：{log_path}")

    return 1 if counts["failed"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
