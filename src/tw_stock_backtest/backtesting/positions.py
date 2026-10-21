"""期末持股的明細（docs/HALT-POLICY.md 3.14、3.16、D8）：股數、估值價與日期、狀態。

停牌中的持股以停牌前收盤估值；結束日以前已公告終止上市的另外標示（公告日不晚於
結束日才標示，不提前）；終止上市後的持股列為無法變現。
"""

from decimal import Decimal

from tw_stock_backtest.typedefs import Delistings, Market, Row

STATUS_LABELS = {
    "NORMAL": "正常",
    "CONFIRMED_HALT": "停牌中",
    "NO_PRICE": "無成交價（原因未確認）",
    "MISSING_ROW": "缺少行情列（原因未確認）",
    "UNREALIZABLE": "無法變現（已終止上市）",
}


def final_positions(
    holdings: dict[str, int],
    unrealizable: dict[str, Row],
    market: Market,
    day: str,
    delistings: Delistings,
    delisted_valuation: str,
) -> list[Row]:
    """回傳期末持股明細，依股票代號排序；金額與估值價保持 Decimal。"""
    result = []
    for stock_id, shares in sorted(holdings.items()):
        record = market[stock_id][day]
        status = record["market_status"]
        price = record["valuation_close"]
        notice = delistings.get(stock_id)
        result.append(
            {
                "stock_id": stock_id,
                "shares": shares,
                "price": price,
                "price_date": record["valuation_price_date"],
                "value": price * shares,
                "status": status,
                "status_label": STATUS_LABELS[status],
                # 結束日以前已公告、但尚未生效的終止上市。
                "delisting_announced": (
                    {"announced": notice["announced"], "effective": notice["effective"]}
                    if notice and notice["announced"] <= day < notice["effective"]
                    else None
                ),
            }
        )
    for stock_id, item in sorted(unrealizable.items()):
        value = (
            Decimal("0")
            if delisted_valuation == "zero" or item["last_close"] is None
            else item["last_close"] * item["shares"]
        )
        result.append(
            {
                "stock_id": stock_id,
                "shares": item["shares"],
                "price": Decimal("0") if delisted_valuation == "zero" else item["last_close"],
                "price_date": item["price_date"],
                "value": value,
                "status": "UNREALIZABLE",
                "status_label": STATUS_LABELS["UNREALIZABLE"],
                "delisting_announced": {
                    "announced": item["announced"],
                    "effective": item["effective"],
                },
            }
        )
    return result
