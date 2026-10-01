from datetime import date
from decimal import Decimal


# 第一版先保存已人工核實的事件。
# 未列入清單的缺價紀錄，仍然視為待釐清問題。
CONFIRMED_HALTS = {
    ("2317", "2025-07-30"): {
        "reason": "重大訊息待公布，暫停交易",
        "source": (
            "https://investoredu.twse.com.tw/"
            "Mobile_Pages/..%2FFileSystem%2FFileUpload%2F"
            "cb744523-2d37-495f-9738-345b882924e7.pdf"
        ),
    },
}


def prepare_market_records(
    records,
    *,
    confirmed_halts,
    allow_unpriced_halt=False,
):
    """保留原始價格，增加交易狀態與估值價格。"""
    prepared = []
    stock_id = None
    previous_date = None
    last_close = None
    last_close_date = None

    for record in records:
        current_date = date.fromisoformat(record["date"])

        if stock_id is None:
            stock_id = record["stock_id"]
        elif record["stock_id"] != stock_id:
            raise ValueError("行情不可混合不同股票")

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("行情日期必須遞增且不可重複")

        previous_date = current_date
        row = record.copy()

        for field in ("volume", "turnover", "trade_count"):
            if field in record:
                value = record[field]

                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise ValueError(f"{field} 必須是非負整數")

        is_halted = (
            stock_id,
            record["date"],
        ) in confirmed_halts

        if is_halted:
            valid = (
                all(
                    field in record and record[field] is None
                    for field in ("open", "high", "low", "close")
                )
                and all(
                    field in record and record[field] == 0
                    for field in ("volume", "turnover", "trade_count")
                )
            )

            if not valid:
                raise ValueError("行情與已確認停牌事件不一致")

            if last_close is None and not allow_unpriced_halt:
                raise ValueError("停牌前沒有有效收盤價可供估值")

        else:
            if "open" not in record or "close" not in record:
                raise ValueError("正常行情至少需要 open 與 close")

            # 接受既有人工測試只提供 open、close 的格式。
            for field in ("open", "high", "low", "close"):
                if field not in record:
                    continue

                value = record[field]

                if (
                    not isinstance(value, Decimal)
                    or not value.is_finite()
                    or value <= 0
                ):
                    raise ValueError(
                        f"{stock_id} / {record['date']} "
                        "有無效價格，且未確認為停牌"
                    )

            if ("high" in record) != ("low" in record):
                raise ValueError("high 與 low 必須一起提供")

            if "high" in record and not (
                record["low"] <= record["open"] <= record["high"]
                and record["low"] <= record["close"] <= record["high"]
            ):
                raise ValueError("高低價範圍異常")

            last_close = record["close"]
            last_close_date = record["date"]

        row.update(
            {
                "tradable": not is_halted,
                "valuation_close": last_close,
                "valuation_price_date": last_close_date,
                "market_status": (
                    "CONFIRMED_HALT" if is_halted else "NORMAL"
                ),
            }
        )

        prepared.append(row)

    return prepared

def prepare_backtest_market(
    records_by_stock,
    start,
    end,
    *,
    confirmed_halts,
):
    """整理各股票行情，保留暖機資料與共同回測日期。"""
    if not records_by_stock:
        raise ValueError("股票池不可為空")

    market = {}
    trading_dates = set()

    for stock_id, records in records_by_stock.items():
        if any(record["stock_id"] != stock_id for record in records):
            raise ValueError(f"{stock_id} 的行情混入其他股票")

        prepared = prepare_market_records(
            [
                record
                for record in records
                if date.fromisoformat(record["date"]) <= end
            ],
            confirmed_halts=confirmed_halts,
            allow_unpriced_halt=True,
        )

        market[stock_id] = {
            record["date"]: record
            for record in prepared
        }

        trading_dates.update(
            record["date"]
            for record in prepared
            if start <= date.fromisoformat(record["date"]) <= end
        )

    if not trading_dates:
        raise ValueError("指定期間沒有可回測的行情")

    for stock_id, prices in market.items():
        for trading_date in trading_dates:
            if trading_date not in prices:
                raise ValueError(
                    f"{stock_id} 缺少 {trading_date} 行情，"
                    "不能將未知缺漏當作停牌"
                )

    return market, sorted(trading_dates)

