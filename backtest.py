from datetime import date
from decimal import Decimal

from costs import CostSettings, calculate_transaction


def run_backtest(
    records,
    signals,
    initial_cash,
    quantity,
    cost_settings=None,
):
    """前日訊號、次筆開盤成交；可傳入交易成本設定。"""
    # 沒有提供設定時，維持原本的零成本模式。
    # 讓舊測試繼續驗證原本的交易邏輯。
    if cost_settings is None:
        cost_settings = CostSettings(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

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
    total_commission = Decimal("0")
    total_tax = Decimal("0")

    for index, record in enumerate(records):
        signal = signals[index - 1] if index > 0 else "NONE"
        open_price = record["open"]

        action = None
        executed_quantity = 0
        transaction = None

        if signal == "BUY" and shares == 0:
            proposed = calculate_transaction(
                open_price,
                quantity,
                "BUY",
                cost_settings,
            )

            # 買入現金變動是負數，取負號得到所需資金。
            required_cash = -proposed["cash_change"]

            if cash >= required_cash:
                transaction = proposed
                shares = quantity
                executed_quantity = quantity
                action = "BUY"

        elif signal == "SELL" and shares > 0:
            executed_quantity = shares

            transaction = calculate_transaction(
                open_price,
                executed_quantity,
                "SELL",
                cost_settings,
            )

            shares = 0
            action = "SELL"

        if transaction is not None:
            cash += transaction["cash_change"]
            total_commission += transaction["commission"]
            total_tax += transaction["tax"]

            trades.append({
                "signal_date": records[index - 1]["date"],
                "date": record["date"],
                "action": action,
                "price": open_price,
                "quantity": executed_quantity,
                "amount": transaction["amount"],
                "commission": transaction["commission"],
                "tax": transaction["tax"],
                "cash_change": transaction["cash_change"],
                "cash_after": cash,
            })

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
        "total_commission": total_commission,
        "total_tax": total_tax,
        "trades": trades,
        "equity_curve": equity_curve,
    }