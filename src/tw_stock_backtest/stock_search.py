"""股票搜尋：個股搜尋頁與股票選擇器共用同一套比對、排序與上限規則。

排序（同一級內依代號排序）：
1. 代號完全符合
2. 代號開頭符合（輸入 23 列出 23xx）
3. 名稱開頭符合
4. 名稱包含（輸入「積電」找到台積電）

比對前把輸入與名稱做 Unicode 正規化（NFKC：全形數字、全形英文轉半形）並忽略大小寫，
所以「２３３０」「ky」也找得到。代號中間的片段（例如 330 對 2330）不算符合。

沒有輸入、也沒有選產業時不列出任何股票（不再預先列出代號最小的幾十檔）。
選了產業時列出該產業所有符合的股票，不套用上限；只有文字搜尋時才套用上限，
並回報總筆數，讓畫面說明「共 N 筆符合，目前顯示前 M 筆」。
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

CODE = re.compile(r"[0-9]{4}")
MAX_QUERY = 40

# 符合方式（越小越前面）與畫面上的說明。
CODE_EXACT, CODE_PREFIX, NAME_PREFIX, NAME_CONTAINS = range(4)
MATCH_LABELS = {
    CODE_EXACT: "代號完全符合",
    CODE_PREFIX: "代號開頭符合",
    NAME_PREFIX: "名稱開頭符合",
    NAME_CONTAINS: "名稱包含",
}


def normalize(text: str | None) -> str:
    """全形轉半形、去掉前後空白、忽略大小寫。"""
    return unicodedata.normalize("NFKC", text or "").strip().casefold()


def match_kind(stock: Mapping[str, Any], query: str) -> int | None:
    """這檔股票對（已正規化的）query 的符合方式；不符合時為 None。"""
    code = stock["stock_id"]
    name = normalize(stock.get("name"))
    if code == query:
        return CODE_EXACT
    if code.startswith(query):
        return CODE_PREFIX
    if name.startswith(query):
        return NAME_PREFIX
    if query in name:
        return NAME_CONTAINS
    return None


def industries(stocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """有產業資料的產業與檔數，依產業名稱排序；沒有產業的股票不列入。"""
    counts = Counter(stock.get("industry") for stock in stocks if stock.get("industry"))
    return [{"name": name, "count": count} for name, count in sorted(counts.items())]


def search(
    stocks: Iterable[Mapping[str, Any]],
    query: str = "",
    *,
    industry: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """依上面的規則搜尋。

    回傳：
    - stocks：要顯示的股票（每筆加上 match，產業瀏覽時沒有文字則為 None），依排序。
    - total：符合的總筆數；truncated：是否因上限只顯示一部分。
    - unlisted_code：輸入是完整四位數代號但名單中沒有時為該代號（仍可開啟或加入）。
    - active：有沒有在搜尋（有文字或有產業）；沒有時畫面改顯示自選、最近查看與說明。
    """
    pool = list(stocks)
    wanted = normalize(query)
    industry = (industry or "").strip()
    candidates = [s for s in pool if not industry or s.get("industry") == industry]

    if wanted:
        ranked = []
        for stock in candidates:
            kind = match_kind(stock, wanted)
            if kind is not None:
                ranked.append((kind, stock["stock_id"], stock))
        ranked.sort(key=lambda item: (item[0], item[1]))
        matches = [{**stock, "match": kind} for kind, _, stock in ranked]
    elif industry:
        matches = [{**s, "match": None} for s in sorted(candidates, key=lambda s: s["stock_id"])]
    else:
        matches = []

    shown = matches if industry else matches[:limit]
    unlisted = (
        wanted
        if CODE.fullmatch(wanted) and not any(s["stock_id"] == wanted for s in pool)
        else None
    )
    return {
        "query": query,
        "industry": industry,
        "active": bool(wanted or industry),
        "total": len(matches),
        "limit": None if industry else limit,
        "truncated": len(shown) < len(matches),
        "stocks": shown,
        "unlisted_code": unlisted,
    }


def summary_text(result: Mapping[str, Any]) -> str:
    """結果數量的說明；超過上限時提示輸入更多字。兩處畫面共用這段文字。"""
    total, shown = result["total"], len(result["stocks"])
    if not result["active"]:
        return ""
    if result["truncated"]:
        return f"共 {total} 筆符合，目前顯示前 {shown} 筆，請輸入更多字縮小範圍"
    if total == 0:
        return "找不到符合的股票"
    return f"共 {total} 筆符合"
