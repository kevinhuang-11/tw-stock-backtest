from datetime import date
from decimal import Decimal

from tw_stock_backtest.backtesting.costs import (
    CostSettings,
    calculate_transaction,
    apply_slippage,
)
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.data.market_events import (
    CONFIRMED_HALTS,
    prepare_backtest_market,
)


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
    confirmed_halts=None,
    slippage_rate=Decimal("0"),
):
    start, end = parse_date_range(start_text, end_text)

    if (
        not isinstance(initial_cash, Decimal)
        or not initial_cash.is_finite()
        or initial_cash <= 0
    ):
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if not isinstance(cost_settings, CostSettings):
        raise ValueError("必須提供 CostSettings")
    
    # 即使沒有成交，也先驗證滑價率。
    apply_slippage(Decimal("1"), "BUY", slippage_rate)

    events = CONFIRMED_HALTS if confirmed_halts is None else confirmed_halts

    market, trading_dates = prepare_backtest_market(
        records_by_stock,
        start,
        end,
        confirmed_halts=events,
    )

    budget = (
        initial_cash * 100 // len(market)
    ) / Decimal("100")

    cash = initial_cash
    holdings = {}
    trades = []
    equity_curve = []
    total_commission = Decimal("0")
    waiting = set(market)
    unbought = []

    for trading_date in trading_dates:
        # 每檔在期間內第一個可交易日，使用保留給它的預算買入。
        for stock_id in sorted(waiting):
            record = market[stock_id][trading_date]

            if not record["tradable"]:
                continue

            execution_price = apply_slippage(
                record["open"],
                "BUY",
                slippage_rate,
            )

            quantity = affordable_quantity(
                execution_price,
                budget,
                cost_settings,
            )

            waiting.remove(stock_id)

            if quantity == 0:
                unbought.append(stock_id)
                continue

            transaction = calculate_transaction(
                execution_price,
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
                    "date": trading_date,
                    "action": "BUY",
                    "raw_price": record["open"],
                    "price": execution_price,
                    "quantity": quantity,
                    **transaction,
                    "cash_after": cash,
                }
            )

        market_value = sum(
            (
                market[stock_id][trading_date]["valuation_close"]
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
                "stale_prices": {
                    stock_id: market[stock_id][trading_date][
                        "valuation_price_date"
                    ]
                    for stock_id in holdings
                    if not market[stock_id][trading_date]["tradable"]
                },
            }
        )

    final_equity = equity_curve[-1]["equity"]

    return {
        "initial_cash": initial_cash,
        "cash": cash,
        "holdings": holdings.copy(),
        "final_equity": final_equity,
        "total_return": final_equity / initial_cash - Decimal("1"),
        "total_commission": total_commission,
        "total_tax": Decimal("0"),
        "trades": trades,
        "equity_curve": equity_curve,
        "unbought": sorted(unbought),
        "unavailable_entire_period": sorted(waiting),
        "slippage_rate": slippage_rate,
    }   
