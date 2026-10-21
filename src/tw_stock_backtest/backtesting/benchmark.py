from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from tw_stock_backtest.backtesting.corporate_actions import Schedule, apply_due
from tw_stock_backtest.backtesting.costs import (
    CostSettings,
    apply_slippage,
    calculate_transaction,
)
from tw_stock_backtest.backtesting.delisting import (
    check_valuation_mode,
    retire_delisted,
    unrealizable_value,
)
from tw_stock_backtest.backtesting.positions import final_positions
from tw_stock_backtest.data.market_events import (
    CONFIRMED_DELISTINGS,
    CONFIRMED_HALTS,
    prepare_backtest_market,
    unconfirmed_gaps,
)
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.typedefs import Action, Delistings, Halts, Record, Row


def affordable_quantity(price: Decimal, budget: Decimal, cost_settings: CostSettings) -> int:
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
    records_by_stock: Mapping[str, Sequence[Record]],
    start_text: str,
    end_text: str,
    *,
    initial_cash: Decimal,
    cost_settings: CostSettings,
    confirmed_halts: Halts | None = None,
    slippage_rate: Decimal = Decimal("0"),
    corporate_actions: Mapping[str, Sequence[Action]] | None = None,
    delistings: Delistings | None = None,
    delisted_valuation: str = "last_close",
) -> dict[str, Any]:
    """各檔平均分配預算，在第一個可交易日開盤買進並持有到期末。

    corporate_actions（{股票: 公司行動}）與多股票引擎使用相同的公司行動帳：
    生效日開盤前依前一日收盤的持股入帳股利、退還股款並調整股數，策略與基準因此可以公平比較。
    """
    start, end = parse_date_range(start_text, end_text)

    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash <= 0:
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if not isinstance(cost_settings, CostSettings):
        raise ValueError("必須提供 CostSettings")

    # 即使沒有成交，也先驗證滑價率。
    apply_slippage(Decimal("1"), "BUY", slippage_rate)

    events = CONFIRMED_HALTS if confirmed_halts is None else confirmed_halts
    delistings = CONFIRMED_DELISTINGS if delistings is None else delistings
    check_valuation_mode(delisted_valuation)

    market, trading_dates = prepare_backtest_market(
        records_by_stock,
        start,
        end,
        confirmed_halts=events,
        delistings=delistings,
    )

    budget = (initial_cash * 100 // len(market)) / Decimal("100")

    cash = initial_cash
    holdings: dict[str, int] = {}
    trades: list[Row] = []
    equity_curve: list[Row] = []
    total_commission = Decimal("0")
    waiting = set(market)
    unbought: list[str] = []

    schedule = Schedule(corporate_actions, "")
    corporate_action_events: list[Row] = []
    # 終止上市的持股與策略相同的規則（docs/HALT-POLICY.md 3.16）。
    unrealizable: dict[str, Row] = {}
    delisting_events: list[Row] = []

    for trading_date in trading_dates:
        received, applied = apply_due(schedule, trading_date, holdings)
        cash += received
        corporate_action_events.extend(applied)
        delisting_events.extend(
            retire_delisted(holdings, unrealizable, market, trading_date, delistings)
        )

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
                market[stock_id][trading_date]["valuation_close"] * quantity
                for stock_id, quantity in holdings.items()
            ),
            Decimal("0"),
        )

        frozen_value = unrealizable_value(unrealizable, delisted_valuation)
        equity_curve.append(
            {
                "date": trading_date,
                "cash": cash,
                "market_value": market_value,
                "unrealizable_value": frozen_value,
                "equity": cash + market_value + frozen_value,
                "holdings": holdings.copy(),
                "stale_prices": {
                    stock_id: market[stock_id][trading_date]["valuation_price_date"]
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
        "corporate_action_events": corporate_action_events,
        "unbought": sorted(unbought),
        "unavailable_entire_period": sorted(waiting),
        "unconfirmed_gaps": unconfirmed_gaps(market),
        "unrealizable": unrealizable,
        "final_positions": final_positions(
            holdings,
            unrealizable,
            market,
            trading_dates[-1],
            delistings,
            delisted_valuation,
        ),
        "delisting_events": delisting_events,
        "delisted_valuation": delisted_valuation,
        "slippage_rate": slippage_rate,
    }
