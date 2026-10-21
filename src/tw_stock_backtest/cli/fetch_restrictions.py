"""下載處置股與全額交割（變更交易方法）的期間，存入行情庫（docs/HALT-POLICY.md 3.17、3.18）。

處置：每個日曆年一次「公布處置有價證券」查詢。全額交割：本機行情庫在期間內的每個
行情日各查一次當天的變更交易名單（沒有行情的日子不查）。總請求數（含重試）超過
--max-requests 時整批放棄、不寫入；--plan 只列出計畫，不連網。
"""

import argparse
import sqlite3
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from tw_stock_backtest.cli.fetch_corporate_actions import (
    BASE,
    BudgetExceeded,
    Client,
    rows_or_empty,
    year_chunks,
)
from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.data import restrictions as tr
from tw_stock_backtest.data.universe import NetworkDisabled, ensure_network_allowed
from tw_stock_backtest.data_status import market_dates
from tw_stock_backtest.date_range import parse_date_range


def disposition_url(start, end):
    query = urlencode(
        {
            "startDate": f"{start:%Y%m%d}",
            "endDate": f"{end:%Y%m%d}",
            "querytype": "",
            "stockNo": "",
            "selectType": "",
            "proceType": "",
            "remarkType": "",
            "sortKind": "DATE",
            "response": "json",
        }
    )
    return f"{BASE}/announcement/punish?{query}"


def full_delivery_url(day):
    return f"{BASE}/fullDelivery/TWT85U?{urlencode({'date': day.replace('-', ''), 'response': 'json'})}"


def plan(db_path, start, end):
    """[(種類, 網址, 參數)]：處置每年一次、全額交割每個本機行情日一次。"""
    requests = [
        (tr.DISPOSITION, disposition_url(chunk_start, chunk_end), (chunk_start, chunk_end))
        for chunk_start, chunk_end in year_chunks(start, end)
    ]
    days = [d for d in market_dates(db_path) if start.isoformat() <= d <= end.isoformat()]
    requests += [(tr.FULL_DELIVERY, full_delivery_url(day), day) for day in days]
    return requests, days


def collect(client, requests):
    """下載並解析；任何失敗都丟出例外，呼叫端不寫入部分結果。"""
    entries, coverage = [], []
    for kind, url, argument in requests:
        payload = rows_or_empty(client.get(url))
        source = url.replace("&response=json", "").replace("response=json&", "")
        if kind == tr.DISPOSITION:
            chunk_start, chunk_end = argument
            first, last = chunk_start.isoformat(), chunk_end.isoformat()
            entries += tr.parse_dispositions(payload, source=source)
            coverage.append((kind, first, last))
        else:
            entries += tr.parse_full_delivery(payload, argument, source=source)
            coverage.append((kind, argument, argument))
    return entries, coverage


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="下載處置股與全額交割（變更交易方法）的期間")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="設定檔位置")
    parser.add_argument("--start", required=True, help="開始日期，例如 2026-08-01")
    parser.add_argument("--end", required=True, help="結束日期，例如 2026-10-14")
    parser.add_argument(
        "--max-requests", type=int, default=100, help="請求上限（含重試），預設 100"
    )
    parser.add_argument("--plan", action="store_true", help="只列出要送的請求，不連網、不寫入")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    try:
        settings = load_config(args.config)
        start, end = parse_date_range(args.start, args.end)
        if args.max_requests <= 0:
            raise ValueError("--max-requests 必須是正整數")
        db_path = settings["storage"]["database_path"]
        requests, days = plan(db_path, start, end)
        if not days:
            raise ValueError("本機行情庫在這段期間沒有行情日，無法決定要查哪幾天的變更交易名單")

        if args.plan:
            dispositions = [url for kind, url, _ in requests if kind == tr.DISPOSITION]
            print(f"期間：{start} ～ {end}")
            for url in dispositions:
                print(f"  處置：{url}")
            print(f"  全額交割：{len(days)} 個行情日（{days[0]} ～ {days[-1]}），每天 1 次")
            print(
                f"合計 {len(requests)} 次請求（不含重試）；上限 {args.max_requests} 次（含重試）。"
            )
            return 0

        if len(requests) > args.max_requests:
            raise BudgetExceeded(
                f"需要 {len(requests)} 次請求，超過上限 {args.max_requests} 次；未連網、未寫入"
            )
        ensure_network_allowed(db_path)
        client = Client(settings["download"], args.max_requests)
        entries, coverage = collect(client, requests)
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tr.save_restrictions(entries, db_path, fetched_at=fetched_at, coverage=coverage)

    except NetworkDisabled as error:
        print(f"未連網：{error}")
        return 1
    except (
        BudgetExceeded,
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        sqlite3.Error,
        OSError,
    ) as error:
        print(f"交易限制下載失敗：{error}")
        return 1

    counts = {kind: len({e["stock_id"] for e in entries if e["kind"] == kind}) for kind in tr.KINDS}
    print(
        f"請求 {client.used} 次；處置 {sum(e['kind'] == tr.DISPOSITION for e in entries)} 筆"
        f"（{counts[tr.DISPOSITION]} 檔），全額交割名單 {len(days)} 天"
        f"（{counts[tr.FULL_DELIVERY]} 檔）（{start} ～ {end}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
