from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast

from tw_stock_backtest.backtesting.corporate_actions import Schedule, apply_due
from tw_stock_backtest.backtesting.costs import CostSettings, calculate_transaction
from tw_stock_backtest.data.market_events import (
    CONFIRMED_HALTS,
    market_event,
    not_tradable_reason,
    prepare_market_records,
)
from tw_stock_backtest.typedefs import Action, Halts, Record, Row


def run_backtest(
    records: Sequence[Record],
    signals: Sequence[str],
    initial_cash: Decimal,
    quantity: int,
    cost_settings: CostSettings | None = None,
    confirmed_halts: Halts | None = None,
    corporate_actions: Sequence[Action] = (),
) -> dict[str, Any]:
    """前日訊號、次筆開盤成交；可傳入交易成本設定。

    corporate_actions 是這檔的公司行動；與多股票引擎相同，生效日開盤前依前一日收盤的持股
    入帳股利與退還股款並調整股數（見 backtesting/corporate_actions.py）。

    停牌與無成交價日使用與多股票回測相同的檢查（prepare_market_records）：
    當日不成交，持股沿用最近有效收盤估值，不補價格。賣出訊號遇到不可交易日時保留，
    在下一個可交易日開盤賣出；買進訊號遇到不可交易日則作廢（docs/HALT-POLICY.md 3.23）。
    """
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

    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash <= 0:
        raise ValueError("初始資金必須是大於 0 的有限 Decimal")

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("買入股數必須是正整數")

    previous_date = None
    # 沒有行情時為 None（迴圈不會執行）；cast 只告訴型別檢查器，不影響執行。
    stock_id = cast(str, records[0]["stock_id"] if records else None)

    for record, signal in zip(records, signals, strict=True):
        current_date = date.fromisoformat(record["date"])

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("行情日期必須由舊到新，且不能重複")

        if record["stock_id"] != stock_id:
            raise ValueError("一次回測只能包含一支股票")

        if signal not in ("BUY", "SELL", "NONE"):
            raise ValueError(f"不支援的訊號：{signal}")

        previous_date = current_date

    events = CONFIRMED_HALTS if confirmed_halts is None else confirmed_halts
    records = prepare_market_records(
        records,
        confirmed_halts=events,
        allow_unpriced_halt=True,
    )

    cash = initial_cash
    shares = 0
    trades: list[Row] = []
    equity_curve: list[Row] = []
    total_commission = Decimal("0")
    total_tax = Decimal("0")
    skipped_orders: list[Row] = []
    market_events: list[Row] = []

    schedule = Schedule({stock_id: list(corporate_actions)}, "")
    corporate_action_events: list[Row] = []
    # 因不可交易而沒賣成的賣出訊號日；之後第一個可交易日開盤賣出。
    sell_intent = None

    for index, record in enumerate(records):
        held = {stock_id: shares} if shares else {}
        received, applied = apply_due(schedule, record["date"], held)
        shares = held.get(stock_id, 0)
        cash += received
        corporate_action_events.extend(applied)

        signal = signals[index - 1] if index > 0 else "NONE"
        open_price = record["open"]

        action = None
        executed_quantity = 0
        transaction = None

        if not record["tradable"]:
            market_events.append(
                {"stock_id": stock_id, "date": record["date"], **market_event(record, events)}
            )

            if signal == "SELL" and shares > 0 and sell_intent is None:
                sell_intent = records[index - 1]["date"]

            if (signal == "BUY" and shares == 0) or (signal == "SELL" and shares > 0):
                skipped_orders.append(
                    {
                        "signal_date": records[index - 1]["date"],
                        "date": record["date"],
                        "action": signal,
                        "reason": f"{not_tradable_reason(record)}，當日不成交",
                    }
                )

        elif signal == "BUY" and shares == 0:
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

        elif (signal == "SELL" or sell_intent is not None) and shares > 0:
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

            trades.append(
                {
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
                    # 不可交易日後重試的賣出：記錄最初的賣出訊號日。
                    **(
                        {"intent_date": sell_intent}
                        if action == "SELL" and sell_intent is not None
                        else {}
                    ),
                }
            )
            if action == "SELL":
                sell_intent = None

        # 不可交易日沿用最近有效收盤；尚未持股時不需要估值價格。
        equity = cash + (record["valuation_close"] * shares if shares else Decimal("0"))

        equity_curve.append(
            {
                "date": record["date"],
                "cash": cash,
                "shares": shares,
                "equity": equity,
                "stale_price_date": (
                    record["valuation_price_date"] if shares and not record["tradable"] else None
                ),
            }
        )

    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_cash

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
        "skipped_orders": skipped_orders,
        "market_events": market_events,
        "corporate_action_events": corporate_action_events,
    }
