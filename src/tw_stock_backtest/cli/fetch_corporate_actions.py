"""下載股票池在指定期間的公司行動（除權息、減資、變更面額），存入行情庫。

只抓股票池需要的期間。每個日曆年送三個列表請求（除權除息、減資、變更面額），
再對股票池內含配股的除權息與每筆減資各送一次詳細資料請求。總請求數（含重試）
超過 --max-requests 時整批放棄、不寫入任何資料；--plan 只列出計畫，不連網。
"""

import argparse
import json
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.market_events import CONFIRMED_HALTS
from tw_stock_backtest.data.universe import NetworkDisabled, ensure_network_allowed
from tw_stock_backtest.date_range import parse_date_range

BASE = "https://www.twse.com.tw/rwd/zh"
LISTS = (
    ("除權除息", "exRight/TWT49U"),
    ("減資", "reducation/TWTAUU"),
    ("變更面額", "change/TWTB8U"),
)
NO_DATA = ("很抱歉，沒有符合條件的資料!", "查無資料")


class BudgetExceeded(ValueError):
    pass


class Client:
    """依設定的間隔與重試次數送出請求，並計算總請求數（含重試）。"""

    def __init__(self, download, max_requests):
        self.download = download
        self.max_requests = max_requests
        self.used = 0

    @staticmethod
    def sleep(seconds):
        # 呼叫時才查 time.sleep，測試可以修補它。
        time.sleep(seconds)

    def get(self, url):
        attempts = self.download["max_attempts"]
        for attempt in range(1, attempts + 1):
            if self.used >= self.max_requests:
                raise BudgetExceeded(f"已達請求上限 {self.max_requests} 次")
            if self.used:
                self.sleep(self.download["request_interval_seconds"])
            self.used += 1
            try:
                return get_json(url, timeout=self.download["timeout_seconds"])
            except HTTPError as error:
                if error.code not in (502, 503, 504) or attempt == attempts:
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == attempts:
                    raise
            self.sleep(self.download["retry_wait_seconds"] * 2 ** (attempt - 1))
        raise AssertionError("unreachable")


def get_json(url, *, timeout):
    request = Request(url, headers={"User-Agent": "tw-stock-backtest/0.1"})
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("公司行動回應格式錯誤，預期為 JSON 物件")
    return result


def year_chunks(start, end):
    """把期間切成每個日曆年一段。"""
    chunks = []
    for year in range(start.year, end.year + 1):
        chunks.append((max(start, date(year, 1, 1)), min(end, date(year, 12, 31))))
    return chunks


def list_url(path, start, end):
    query = urlencode(
        {"startDate": f"{start:%Y%m%d}", "endDate": f"{end:%Y%m%d}", "response": "json"}
    )
    return f"{BASE}/{path}?{query}"


def rows_or_empty(payload):
    """沒有符合條件的資料時視為空列表；其他非 OK 回應仍然失敗。"""
    if isinstance(payload, dict) and payload.get("stat") in NO_DATA:
        return {"stat": "OK", "fields": [], "data": [], "notes": payload.get("notes") or []}
    return payload


def collect(client, stocks, start, end):
    """下載並解析；任何請求或解析失敗都會丟出例外，呼叫端不寫入部分結果。"""
    pending = []  # (種類, 列, 詳細資料網址或 None, 其他參數)
    for chunk_start, chunk_end in year_chunks(start, end):
        dividends = rows_or_empty(client.get(list_url("exRight/TWT49U", chunk_start, chunk_end)))
        url = list_url("exRight/TWT49U", chunk_start, chunk_end).replace("&response=json", "")
        for row, (detail_stock, detail_date), needs_detail in ca.dividend_rows(dividends, stocks):
            detail = (
                f"{BASE}/exRight/TWT49UDetail?STK_NO={detail_stock}&T1={detail_date}&response=json"
                if needs_detail
                else None
            )
            pending.append((ca.DIVIDEND, row, detail, {"list_url": url}))

        reductions = rows_or_empty(
            client.get(list_url("reducation/TWTAUU", chunk_start, chunk_end))
        )
        url = list_url("reducation/TWTAUU", chunk_start, chunk_end).replace("&response=json", "")
        rows, combined = ca.reduction_rows(reductions, stocks)
        for row, (detail_stock, detail_date) in rows:
            detail = f"{BASE}/reducation/TWTAVUDetail?STK_NO={detail_stock}&FILE_DATE={detail_date}&response=json"
            pending.append(
                (
                    ca.CAPITAL_REDUCTION,
                    row,
                    detail,
                    {"list_url": url, "combined_dividend": combined.get(ca._stock(row))},
                )
            )

        changes = rows_or_empty(client.get(list_url("change/TWTB8U", chunk_start, chunk_end)))
        url = list_url("change/TWTB8U", chunk_start, chunk_end).replace("&response=json", "")
        for row in ca.par_change_rows(changes, stocks):
            pending.append((ca.PAR_CHANGE, row, None, {"list_url": url}))

    details = sum(detail is not None for _, _, detail, _ in pending)
    if client.used + details > client.max_requests:
        raise BudgetExceeded(
            f"列表已用 {client.used} 次請求，詳細資料還需要 {details} 次，"
            f"合計超過上限 {client.max_requests} 次；未寫入任何資料"
        )

    actions = []
    incomplete = []  # 前收或參考價空白：不寫入，列給人工確認，其他筆照常處理
    for kind, row, detail_url, options in pending:
        detail = client.get(detail_url) if detail_url else None
        try:
            if kind == ca.DIVIDEND:
                actions.append(ca.parse_dividend(row, detail, **options))
            elif kind == ca.CAPITAL_REDUCTION:
                actions.append(ca.parse_reduction(row, detail, **options))
            else:
                actions.append(ca.parse_par_change(row, **options))
        except ca.IncompleteSourceRow as error:
            incomplete.append(error.item)

    first, last = start.isoformat(), end.isoformat()
    actions = [a for a in actions if first <= a["effective_date"] <= last]
    incomplete = [a for a in incomplete if first <= a["effective_date"] <= last]
    return ca.check_halts(actions, CONFIRMED_HALTS), incomplete


def recheck_stored(args):
    """以目前規則重新檢查已儲存的公司行動，只更新 needs_review 與 review_notes。"""
    try:
        settings = load_config(args.config)
        db_path = settings["storage"]["database_path"]
        changed = []
        for action in ca.load_actions(db_path):
            fresh = ca.recheck(action, CONFIRMED_HALTS)
            if (fresh["needs_review"], fresh["review_notes"]) != (
                action["needs_review"],
                action["review_notes"],
            ):
                changed.append(fresh)
        ca.update_review(changed, db_path)
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"重新檢查失敗：{error}")
        return 1
    print(f"重新檢查完成；更新 {len(changed)} 筆待人工確認的標記")
    for action in changed:
        state = "待人工確認" if action["needs_review"] else "不需確認"
        print(f"{action['effective_date']} | {action['stock_id']} | {state}")
    return 0


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="下載股票池在指定期間的除權息、減資與變更面額資料")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="設定檔位置")
    parser.add_argument("--stocks", nargs="+", help="股票代號；未指定時使用設定檔的股票池")
    parser.add_argument("--start", help="開始日期，例如 2026-01-01")
    parser.add_argument("--end", help="結束日期，例如 2026-10-15")
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="不連網：以目前的解析規則重新檢查已儲存的公司行動，更新待人工確認的標記與原因",
    )
    parser.add_argument("--max-requests", type=int, default=50, help="請求上限（含重試），預設 50")
    parser.add_argument("--plan", action="store_true", help="只列出要送的請求，不連網、不寫入")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    if args.recheck:
        return recheck_stored(args)
    try:
        settings = load_config(args.config)
        if not args.start or not args.end:
            raise ValueError("下載需要 --start 與 --end")
        start, end = parse_date_range(args.start, args.end)
        stocks = args.stocks or settings["universe"]["stocks"]
        if not stocks or any(not re.fullmatch(r"\d{4}", s) for s in stocks):
            raise ValueError("股票代號需為四位數")
        if args.max_requests <= 0:
            raise ValueError("--max-requests 必須是正整數")
        db_path = settings["storage"]["database_path"]
        chunks = year_chunks(start, end)

        if args.plan:
            print(f"股票：{'、'.join(stocks)}；期間：{start} ～ {end}")
            for chunk_start, chunk_end in chunks:
                for name, path in LISTS:
                    print(f"  {name}：{list_url(path, chunk_start, chunk_end)}")
            print(
                f"列表請求 {len(chunks) * len(LISTS)} 次；詳細資料依列表結果而定："
                "股票池中每筆含配股的除權息與每筆減資各 1 次。上限 "
                f"{args.max_requests} 次（含重試）。"
            )
            return 0

        ensure_network_allowed(db_path)
        client = Client(settings["download"], args.max_requests)
        actions, incomplete = collect(client, set(stocks), start, end)
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # 有未寫入的列的股票不記錄已下載期間：報表會把這段期間列為未涵蓋，
        # 不會當成「沒有公司行動」。人工確認後重新下載即可補上。
        unsure = {item["stock_id"] for item in incomplete}
        coverage = [
            (stock, start.isoformat(), end.isoformat())
            for stock in sorted(set(stocks))
            if stock not in unsure
        ]
        ca.save_actions(actions, db_path, fetched_at=fetched_at, coverage=coverage)

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
        print(f"公司行動下載失敗：{error}")
        return 1

    print(f"請求 {client.used} 次；寫入 {len(actions)} 筆公司行動（{start} ～ {end}）")
    names = {ca.DIVIDEND: "除權息", ca.CAPITAL_REDUCTION: "減資", ca.PAR_CHANGE: "變更面額"}
    for action in actions:
        flag = "（待人工確認）" if action["needs_review"] else ""
        print(f"{action['effective_date']} | {action['stock_id']} | {names[action['kind']]}{flag}")
        for note in action["review_notes"]:
            print(f"    {note}")
    if incomplete:
        print(f"需要人工確認、未寫入：{len(incomplete)} 筆（這幾檔的下載期間不記為已涵蓋）")
        for item in incomplete:
            missing = "、".join(f"{name}空白" for name in item["missing"])
            print(
                f"{item['effective_date']} | {item['stock_id']} | {names[item['kind']]} | {missing}"
                f" | {item['sources'][0]}"
            )
        # 沿用研究類 CLI 的約定：部分完成、其餘結果有效時退出碼 2。
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
