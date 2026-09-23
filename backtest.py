from datetime import date
from decimal import Decimal


def run_backtest(records, signals, initial_cash, quantity):
    """簡化回測：前日訊號、次筆開盤成交，暫不計交易成本。"""
    if len(records) != len(signals):
        raise ValueError("行情與訊號的長度必須相同")

    if (
        not isinstance(initial_cash, Decimal)
        or not initial_cash.is_finite()
        or initial_cash <= 0
    ):
        raise ValueError("初始資金必須是大於 0 的有限 Decimal")

    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
    ):
        raise ValueError("買入股數必須是正整數")

    # 先檢查全部資料，避免使用不完整或排序錯誤的行情。
    previous_date = None
    stock_id = records[0]["stock_id"] if records else None

    for record, signal in zip(records, signals):
        current_date = date.fromisoformat(record["date"])

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("行情日期必須由舊到新，且不能重複")

        if record["stock_id"] != stock_id:
            raise ValueError("一次回測只能包含一支股票")

        for key in ("open", "close"):
            price = record[key]

            if (
                not isinstance(price, Decimal)
                or not price.is_finite()
                or price <= 0
            ):
                raise ValueError(f"{record['date']} 的 {key} 無效")

        if signal not in ("BUY", "SELL", "NONE"):
            raise ValueError(f"不支援的訊號：{signal}")

        previous_date = current_date

    cash = initial_cash
    shares = 0
    trades = []
    equity_curve = []

    for index, record in enumerate(records):
        # 第一天沒有區間內的前日訊號。
        signal = signals[index - 1] if index > 0 else "NONE"

        open_price = record["open"]
        executed_quantity = 0
        action = None

        if signal == "BUY" and shares == 0:
            cost = open_price * quantity

            if cash >= cost:
                cash -= cost
                shares = quantity
                executed_quantity = quantity
                action = "BUY"

        elif signal == "SELL" and shares > 0:
            executed_quantity = shares
            cash += open_price * shares
            shares = 0
            action = "SELL"

        if action is not None:
            trades.append({
                "signal_date": records[index - 1]["date"],
                "date": record["date"],
                "action": action,
                "price": open_price,
                "quantity": executed_quantity,
                "cash_after": cash,
            })

        # 每天收盤時的總資產：現金＋持股市值。
        equity = cash + record["close"] * shares

        equity_curve.append({
            "date": record["date"],
            "cash": cash,
            "shares": shares,
            "equity": equity,
        })

    final_equity = (
        equity_curve[-1]["equity"]
        if equity_curve
        else initial_cash
    )

    return {
        "initial_cash": initial_cash,
        "cash": cash,
        "shares": shares,
        "final_equity": final_equity,
        "total_return": final_equity / initial_cash - Decimal("1"),
        "trades": trades,
        "equity_curve": equity_curve,
    }