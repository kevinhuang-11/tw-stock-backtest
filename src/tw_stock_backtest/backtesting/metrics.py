from datetime import date
from decimal import Decimal


def summarize_performance(equity_curve, initial_cash):
    """根據每日收盤資產，計算基本績效統計。"""
    if (
        not isinstance(initial_cash, Decimal)
        or not initial_cash.is_finite()
        or initial_cash <= 0
    ):
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if not equity_curve:
        raise ValueError("每日資產紀錄不可為空")

    # 把初始資金也當作可能的歷史高點，
    # 才不會漏掉第一個交易日發生的虧損。
    running_peak = initial_cash
    max_drawdown = Decimal("0")

    highest_equity = None
    lowest_equity = None
    highest_date = None
    lowest_date = None

    previous_date = None
    invested_days = 0

    for point in equity_curve:
        current_date = date.fromisoformat(point["date"])
        equity = point["equity"]
        holdings = point["holdings"]

        if (
            previous_date is not None
            and current_date <= previous_date
        ):
            raise ValueError("資產紀錄日期必須遞增且不可重複")

        previous_date = current_date

        if (
            not isinstance(equity, Decimal)
            or not equity.is_finite()
            or equity < 0
        ):
            raise ValueError("總資產必須是非負且有限的 Decimal")

        if not isinstance(holdings, dict):
            raise ValueError("持股紀錄必須是字典")

        for quantity in holdings.values():
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity <= 0
            ):
                raise ValueError("持股紀錄中的股數必須是正整數")

        if holdings:
            invested_days += 1

        if highest_equity is None or equity > highest_equity:
            highest_equity = equity
            highest_date = point["date"]

        if lowest_equity is None or equity < lowest_equity:
            lowest_equity = equity
            lowest_date = point["date"]

        running_peak = max(running_peak, equity)

        drawdown = (
            running_peak - equity
        ) / running_peak

        max_drawdown = max(max_drawdown, drawdown)

    total_days = len(equity_curve)
    final_equity = equity_curve[-1]["equity"]

    return {
        "total_return": final_equity / initial_cash - Decimal("1"),
        "max_drawdown": max_drawdown,
        "highest_equity": highest_equity,
        "highest_date": highest_date,
        "lowest_equity": lowest_equity,
        "lowest_date": lowest_date,
        "total_days": total_days,
        "invested_days": invested_days,
        "cash_only_days": total_days - invested_days,
        "invested_day_ratio": (
            Decimal(invested_days) / Decimal(total_days)
        ),
    }

def summarize_exposure(equity_curve):
    """計算每日收盤持股市值占比，包含空手日。"""
    if not equity_curve:
        raise ValueError("資產曲線不可為空")

    ratios = []

    for point in equity_curve:
        equity = point.get("equity")
        market_value = point.get("market_value")

        for name, value in (
            ("總資產", equity),
            ("持股市值", market_value),
        ):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
            ):
                raise ValueError(
                    f"{name}必須是非負的有限 Decimal"
                )

        if market_value > equity:
            raise ValueError("持股市值不可超過總資產")

        ratio = (
            market_value / equity
            if equity > 0
            else Decimal("0")
        )
        ratios.append(ratio)

    return {
        "average_exposure": (
            sum(ratios, Decimal("0")) / Decimal(len(ratios))
        ),
        "max_exposure": max(ratios),
    }

