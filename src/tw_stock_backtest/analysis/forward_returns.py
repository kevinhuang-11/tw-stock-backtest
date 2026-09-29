from datetime import date
from decimal import Decimal


def _valid_price(value):
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value > 0
    )


def evaluate_forward_return(
    records,
    signal_date,
    trading_dates,
    *,
    horizon,
):
    """
    計算訊號日之後的價格報酬。

    起算：下一行情日開盤。
    結束：進場日起第 horizon 個行情日收盤。
    進場日算第 1 日。

    trading_dates 必須是共用、嚴格遞增的行情日期清單。
    本函式只評估價格，不包含費稅、滑價、股利或拆股。
    """
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or horizon <= 0
    ):
        raise ValueError("horizon 必須是正整數")

    signal = date.fromisoformat(signal_date)

    calendar = [
        date.fromisoformat(value)
        for value in trading_dates
    ]

    if any(
        current <= previous
        for previous, current in zip(calendar, calendar[1:])
    ):
        raise ValueError("行情日期必須嚴格遞增且不可重複")

    if signal not in calendar:
        raise ValueError("訊號日不在行情日期清單內")

    # 建立單一股票的日期索引，不修改原始資料。
    by_date = {}
    stock_ids = set()

    for record in records:
        record_date = date.fromisoformat(record["date"])

        if record_date in by_date:
            raise ValueError("股票行情日期不可重複")

        stock_ids.add(record["stock_id"])
        by_date[record_date] = record

    if len(stock_ids) > 1:
        raise ValueError("一次只能評估一檔股票")

    signal_index = calendar.index(signal)
    entry_index = signal_index + 1
    exit_index = signal_index + horizon

    result = {
        "stock_id": next(iter(stock_ids), None),
        "signal_date": signal.isoformat(),
        "horizon": horizon,
        "entry_date": None,
        "exit_date": None,
        "entry_price": None,
        "exit_price": None,
        "price_return": None,
        "status": "unavailable",
        "reason": None,
    }

    if entry_index >= len(calendar):
        result["reason"] = "insufficient_future_dates"
        return result

    entry_date = calendar[entry_index]
    result["entry_date"] = entry_date.isoformat()

    if exit_index < len(calendar):
        result["exit_date"] = calendar[exit_index].isoformat()

    entry_record = by_date.get(entry_date)

    if entry_record is None:
        result["reason"] = "missing_entry_record"
        return result

    entry_price = entry_record.get("open")

    if (
        entry_record.get("tradable") is False
        or not _valid_price(entry_price)
    ):
        result["reason"] = "unavailable_entry_price"
        return result

    result["entry_price"] = entry_price

    if exit_index >= len(calendar):
        result["reason"] = "insufficient_future_dates"
        return result

    exit_date = calendar[exit_index]
    exit_record = by_date.get(exit_date)

    if exit_record is None:
        result["reason"] = "missing_exit_record"
        return result

    exit_price = exit_record.get("close")

    if (
        exit_record.get("tradable") is False
        or not _valid_price(exit_price)
    ):
        result["reason"] = "unavailable_exit_price"
        return result

    result["exit_price"] = exit_price
    result["price_return"] = (
        exit_price / entry_price - Decimal("1")
    )
    result["status"] = "ok"

    return result

def evaluate_rankings(
    ranking_history,
    records_by_stock,
    trading_dates,
    *,
    horizons=(5, 20, 60),
    top_ns=(1, 2),
):
    """比較每日候選股與股票池的等權平均後續價格報酬。"""
    if not records_by_stock:
        raise ValueError("股票池不可為空")

    for name, values in (
        ("horizons", horizons),
        ("top_ns", top_ns),
    ):
        if (
            not isinstance(values, (list, tuple))
            or not values
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                for value in values
            )
            or len(set(values)) != len(values)
        ):
            raise ValueError(f"{name} 必須是非空且不重複的正整數序列")

    for stock_id, records in records_by_stock.items():
        if any(row["stock_id"] != stock_id for row in records):
            raise ValueError("行情中的股票代號與股票池不一致")

    stocks = list(records_by_stock)
    details = []
    previous_date = None

    for day in ranking_history:
        signal_date = day["date"]
        current_date = date.fromisoformat(signal_date)

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("排名日期必須嚴格遞增且不可重複")
        previous_date = current_date

        ranked_ids = [
            candidate["stock_id"]
            for candidate in day["candidates"]
        ]

        if len(set(ranked_ids)) != len(ranked_ids):
            raise ValueError("候選排名不可重複")

        if any(stock_id not in records_by_stock for stock_id in ranked_ids):
            raise ValueError("候選股票不在股票池內")

        for horizon in horizons:
            forward = {}

            if not day.get("errors") and ranked_ids:
                forward = {
                    stock_id: evaluate_forward_return(
                        records_by_stock[stock_id],
                        signal_date,
                        trading_dates,
                        horizon=horizon,
                    )
                    for stock_id in stocks
                }

            for top_n in top_ns:
                selected = ranked_ids[:top_n]

                row = {
                    "signal_date": signal_date,
                    "horizon": horizon,
                    "top_n": top_n,
                    "selected_stocks": selected,
                    "selected_count": len(selected),
                    "status": "unavailable",
                    "reason": None,
                    "unavailable_stocks": {},
                    "selected_return": None,
                    "universe_return": None,
                    "excess_return": None,
                }

                if day.get("errors"):
                    row["reason"] = "ranking_incomplete"

                elif not selected:
                    row["reason"] = "no_candidates"

                else:
                    unavailable = {
                        stock_id: result["reason"]
                        for stock_id, result in forward.items()
                        if result["status"] != "ok"
                    }

                    if unavailable:
                        row["reason"] = "incomplete_forward_returns"
                        row["unavailable_stocks"] = unavailable

                    else:
                        selected_return = sum(
                            (
                                forward[stock_id]["price_return"]
                                for stock_id in selected
                            ),
                            Decimal("0"),
                        ) / Decimal(len(selected))

                        universe_return = sum(
                            (
                                result["price_return"]
                                for result in forward.values()
                            ),
                            Decimal("0"),
                        ) / Decimal(len(stocks))

                        row.update({
                            "status": "ok",
                            "selected_return": selected_return,
                            "universe_return": universe_return,
                            "excess_return": (
                                selected_return - universe_return
                            ),
                        })

                details.append(row)

    summaries = []

    for horizon in horizons:
        for top_n in top_ns:
            group = [
                row for row in details
                if row["horizon"] == horizon
                and row["top_n"] == top_n
            ]
            valid = [
                row for row in group
                if row["status"] == "ok"
            ]

            reason_counts = {}
            for row in group:
                if row["status"] != "ok":
                    reason = row["reason"]
                    reason_counts[reason] = (
                        reason_counts.get(reason, 0) + 1
                    )

            def average(key):
                if not valid:
                    return None

                return sum(
                    (row[key] for row in valid),
                    Decimal("0"),
                ) / Decimal(len(valid))

            summaries.append({
                "horizon": horizon,
                "top_n": top_n,
                "signal_days": len(group),
                "evaluated_days": len(valid),
                "unavailable_days": len(group) - len(valid),
                "unavailable_reasons": reason_counts,
                "average_selected_return": average("selected_return"),
                "average_universe_return": average("universe_return"),
                "average_excess_return": average("excess_return"),
                "positive_return_ratio": (
                    Decimal(sum(
                        row["selected_return"] > 0
                        for row in valid
                    )) / Decimal(len(valid))
                    if valid else None
                ),
                "outperform_ratio": (
                    Decimal(sum(
                        row["excess_return"] > 0
                        for row in valid
                    )) / Decimal(len(valid))
                    if valid else None
                ),
            })

    return {
        "details": details,
        "summaries": summaries,
    }

