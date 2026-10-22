"""終止上市的持股：移到「無法變現」，不再占持股名額（docs/HALT-POLICY.md 3.16）。

終止上市日開盤前，持有中的股票從可交易的持股移出，記錄股數、最後一筆有效收盤與價格日期。
估值依設定 [halts] delisted_valuation：
- last_close（預設）：以最後一筆有效收盤凍結估值。
- zero：壓力測試，從終止上市日起估值為 0。
這筆金額計入總資產，但不是現金，不能用來買進。
"""

from decimal import Decimal

from tw_stock_backtest.data.market_events import DELISTING_KINDS
from tw_stock_backtest.typedefs import Delistings, Market, Row

VALUATION_MODES = ("last_close", "zero")


def check_valuation_mode(mode: str) -> None:
    if mode not in VALUATION_MODES:
        raise ValueError("delisted_valuation 必須是 last_close 或 zero")


def final_valuation_note(mode: str) -> str:
    """期末持股的估值說明（不含結尾句號），CLI 開頭與回測報表頁共用。"""
    delisted = "以 0 估值，壓力測試" if mode == "zero" else "以最後收盤凍結估值"
    return (
        "期末不強制賣出，以結束日收盤估值。停牌、無成交價或缺列的持股沿用最近一筆收盤並標示日期；"
        f"已終止上市的列為無法變現（{delisted}），不占持股名額也不能動用"
    )


def retire_delisted(
    holdings: dict[str, int],
    unrealizable: dict[str, Row],
    market: Market,
    day: str,
    delistings: Delistings,
) -> list[Row]:
    """把 day 已終止上市的持股移到 unrealizable，回傳這天移出的明細。"""
    events = []
    for stock_id in sorted(holdings):
        record = market[stock_id][day]
        if record["market_status"] != "DELISTED":
            continue
        shares = holdings.pop(stock_id)
        info = delistings[stock_id]
        entry = {
            "stock_id": stock_id,
            "shares": shares,
            "last_close": record["valuation_close"],
            "price_date": record["valuation_price_date"],
            "announced": info["announced"],
            "effective": info["effective"],
            "kind": info["kind"],
            "kind_label": DELISTING_KINDS[info["kind"]],
            "reason": info["reason"],
            "consideration": info.get("consideration"),
            "sources": info["sources"],
        }
        unrealizable[stock_id] = entry
        events.append({**entry, "date": day})
    return events


def unrealizable_value(unrealizable: dict[str, Row], mode: str) -> Decimal:
    """無法變現部位的估值；zero 模式為 0。最後收盤不存在時也是 0（從未有成交價）。"""
    if mode == "zero":
        return Decimal("0")
    return sum(
        (
            entry["last_close"] * entry["shares"]
            for entry in unrealizable.values()
            if entry["last_close"] is not None
        ),
        Decimal("0"),
    )
