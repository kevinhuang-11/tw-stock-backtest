from datetime import date
from decimal import Decimal

from tw_stock_backtest.backtesting.costs import (
    CostSettings,
    calculate_transaction,
)
from tw_stock_backtest.date_range import parse_date_range


def affordable_quantity(price, budget, cost_settings):
    """使用二分搜尋，找出包含手續費後能負擔的最大整數股數。"""
    low = 0
    high = int(budget // price)

    while low < high:
        middle = (low + high + 1) // 2

        transaction = calculate_transaction(
            price,
            middle,
            "BUY",
            cost_settings,
        )

        required_cash = -transaction["cash_change"]

        if required_cash <= budget:
            low = middle
        else:
            high = middle - 1

    return low


def run_buy_and_hold(
    records_by_stock,
    start_text,
    end_text,
    *,
    initial_cash,
    cost_settings,
):
    """等預算買入股票池，持有到期末的簡化基準。"""
    start, end = parse_date_range(start_text, end_text)

    if not records_by_stock:
        raise ValueError("股票池不可為空")

    if (
        not isinstance(initial_cash, Decimal)
        or not initial_cash.is_finite()
        or initial_cash <= 0
    ):
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if not isinstance(cost_settings, CostSettings):
        raise ValueError("必須提供 CostSettings")

    prices_by_stock = {}
    all_dates = set()

    for stock_id, records in records_by_stock.items():
        selected = {}
        previous_date = None

        for record in records:
            current_date = date.fromisoformat(record["date"])

            if record["stock_id"] != stock_id:
                raise ValueError(f"{stock_id} 的行情混入其他股票")

            if (
                previous_date is not None
                and current_date <= previous_date
            ):
                raise ValueError("行情日期必須遞增且不可重複")

            previous_date = current_date

            if start <= current_date <= end:
                for field in ("open", "close"):
                    value = record[field]

                    if (
                        not isinstance(value, Decimal)
                        or not value.is_finite()
                        or value <= 0
                    ):
                        raise ValueError(
                            f"{stock_id} / {record['date']} "
                            f"的 {field} 必須是正值且有限的 Decimal"
                        )

                selected[record["date"]] = record
                all_dates.add(record["date"])

        prices_by_stock[stock_id] = selected

    if not all_dates:
        raise ValueError("指定期間沒有可回測的行情")

    trading_dates = sorted(all_dates)

    for stock_id, prices in prices_by_stock.items():
        for trading_date in trading_dates:
            if trading_date not in prices:
                raise ValueError(
                    f"{stock_id} 缺少 {trading_date} 行情"
                )

    # 每檔預算無條件捨去到分，避免分配總額超過初始資金。
    stock_count = len(prices_by_stock)
    budget = (
        (initial_cash * 100 // stock_count) / Decimal("100")
    )

    cash = initial_cash
    holdings = {}
    trades = []
    unbought = []
    total_commission = Decimal("0")

    first_date = trading_dates[0]

    for stock_id in sorted(prices_by_stock):
        price = prices_by_stock[stock_id][first_date]["open"]

        quantity = affordable_quantity(
            price,
            budget,
            cost_settings,
        )

        if quantity == 0:
            unbought.append(stock_id)
            continue

        transaction = calculate_transaction(
            price,
            quantity,
            "BUY",
            cost_settings,
        )

        cash += transaction["cash_change"]
        holdings[stock_id] = quantity
        total_commission += transaction["commission"]

        trades.append(
            {
                "stock_id": stock_id,
                "date": first_date,
                "action": "BUY",
                "price": price,
                "quantity": quantity,
                **transaction,
                "cash_after": cash,
            }
        )

    equity_curve = []

    for trading_date in trading_dates:
        market_value = sum(
            (
                prices_by_stock[stock_id][trading_date]["close"]
                * quantity
                for stock_id, quantity in holdings.items()
            ),
            Decimal("0"),
        )

        equity_curve.append(
            {
                "date": trading_date,
                "cash": cash,
                "market_value": market_value,
                "equity": cash + market_value,
                "holdings": holdings.copy(),
            }
        )

    final_equity = equity_curve[-1]["equity"]

    return {
        "initial_cash": initial_cash,
        "cash": cash,
        "holdings": holdings,
        "final_equity": final_equity,
        "total_return": final_equity / initial_cash - Decimal("1"),
        "total_commission": total_commission,
        "total_tax": Decimal("0"),
        "trades": trades,
        "equity_curve": equity_curve,
        "unbought": unbought,
    }