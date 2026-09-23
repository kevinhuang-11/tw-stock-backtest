from datetime import date
from decimal import Decimal

from tw_stock_backtest.analysis.screening_history import (
    build_screening_history,
)
from tw_stock_backtest.backtesting.costs import (
    CostSettings,
    calculate_transaction,
)
from tw_stock_backtest.date_range import parse_date_range



def plan_rebalance(
    holdings,
    ranked_stock_ids,
    *,
    max_positions,
):
    """
    根據目前持股與候選排名，產生持股調整計畫。

    holdings：
        股票代號對應持有股數，例如 {"2330": 100}。

    ranked_stock_ids：
        已完成排名的候選股票代號。

    本函式不執行交易、不檢查現金，也不修改輸入。
    """
    if (
        isinstance(max_positions, bool)
        or not isinstance(max_positions, int)
        or max_positions <= 0
    ):
        raise ValueError("max_positions 必須是正整數")

    if not isinstance(holdings, dict):
        raise ValueError("holdings 必須是字典")

    if not isinstance(ranked_stock_ids, (list, tuple)):
        raise ValueError("候選排名必須是清單或 tuple")

    for stock_id, quantity in holdings.items():
        if (
            not isinstance(stock_id, str)
            or not stock_id
            or stock_id != stock_id.strip()
        ):
            raise ValueError("持股代號必須是非空且無前後空白的字串")

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
        ):
            raise ValueError("持股股數必須是正整數")

    for stock_id in ranked_stock_ids:
        if (
            not isinstance(stock_id, str)
            or not stock_id
            or stock_id != stock_id.strip()
        ):
            raise ValueError("候選代號必須是非空且無前後空白的字串")

    if len(set(ranked_stock_ids)) != len(ranked_stock_ids):
        raise ValueError("候選排名不可包含重複股票")

    target = list(ranked_stock_ids[:max_positions])
    target_set = set(target)
    held_set = set(holdings)

    # 賣出順序固定，方便重現與測試。
    sell = sorted(held_set - target_set)

    # 買進順序保留候選排名。
    buy = [
        stock_id
        for stock_id in target
        if stock_id not in held_set
    ]

    keep = [
        stock_id
        for stock_id in target
        if stock_id in held_set
    ]

    return {
        "target": target,
        "sell": sell,
        "buy": buy,
        "keep": keep,
    }

def run_portfolio_backtest(
    records_by_stock,
    start_text,
    end_text,
    *,
    screening_settings,
    initial_cash,
    quantity,
    max_positions,
    cost_settings,
):
    """每日選股，下一行情日開盤調整持股的簡化組合回測。"""
    start, end = parse_date_range(start_text, end_text)

    if not records_by_stock:
        raise ValueError("股票池不可為空")

    if (
        not isinstance(initial_cash, Decimal)
        or not initial_cash.is_finite()
        or initial_cash <= 0
    ):
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
    ):
        raise ValueError("每次買進股數必須是正整數")

    if not isinstance(cost_settings, CostSettings):
        raise ValueError("必須提供 CostSettings")

    # 沿用持股計畫函式，驗證持股上限。
    plan_rebalance({}, [], max_positions=max_positions)

    screening_history = build_screening_history(
        records_by_stock,
        start.isoformat(),
        end.isoformat(),
        screening_settings=screening_settings,
    )

    if not screening_history:
        raise ValueError("指定期間沒有可回測的行情")

    trading_dates = [
        day["date"]
        for day in screening_history
    ]

    # 建立查詢索引，並驗證回測期間的價格。
    prices_by_stock = {}

    for stock_id, records in records_by_stock.items():
        prices_by_stock[stock_id] = {
            record["date"]: record
            for record in records
            if start <= date.fromisoformat(record["date"]) <= end
        }

        for trading_date in trading_dates:
            record = prices_by_stock[stock_id].get(trading_date)

            if record is None:
                raise ValueError(
                    f"{stock_id} 缺少 {trading_date} 行情，"
                    "本版不模擬缺行情或停牌時的成交與估值"
                )

            for field in ("open", "close"):
                value = record[field]

                if (
                    not isinstance(value, Decimal)
                    or not value.is_finite()
                    or value <= 0
                ):
                    raise ValueError(
                        f"{stock_id} / {trading_date} "
                        f"的 {field} 必須是正值且有限的 Decimal"
                    )

    cash = initial_cash
    holdings = {}
    trades = []
    skipped_orders = []
    skipped_rebalances = []
    equity_curve = []
    total_commission = Decimal("0")
    total_tax = Decimal("0")

    # 存放上一行情日收盤後產生的計畫。
    pending = None

    for day in screening_history:
        trading_date = day["date"]

        if pending is not None:
            signal_date = pending["signal_date"]
            plan = pending["plan"]

            # 先賣出，釋放資金與持股名額。
            for stock_id in plan["sell"]:
                shares = holdings[stock_id]
                price = prices_by_stock[stock_id][trading_date]["open"]

                transaction = calculate_transaction(
                    price,
                    shares,
                    "SELL",
                    cost_settings,
                )

                cash += transaction["cash_change"]
                del holdings[stock_id]

                total_commission += transaction["commission"]
                total_tax += transaction["tax"]

                trades.append(
                    {
                        "stock_id": stock_id,
                        "signal_date": signal_date,
                        "date": trading_date,
                        "action": "SELL",
                        "price": price,
                        "quantity": shares,
                        **transaction,
                        "cash_after": cash,
                    }
                )

            # 再依候選排名買進。
            for stock_id in plan["buy"]:
                price = prices_by_stock[stock_id][trading_date]["open"]

                transaction = calculate_transaction(
                    price,
                    quantity,
                    "BUY",
                    cost_settings,
                )

                reason = None

                if len(holdings) >= max_positions:
                    reason = "已達持股上限"
                elif cash < -transaction["cash_change"]:
                    reason = "資金不足，含手續費"

                if reason is not None:
                    skipped_orders.append(
                        {
                            "stock_id": stock_id,
                            "signal_date": signal_date,
                            "date": trading_date,
                            "reason": reason,
                        }
                    )
                    continue

                cash += transaction["cash_change"]
                holdings[stock_id] = quantity

                total_commission += transaction["commission"]
                total_tax += transaction["tax"]

                trades.append(
                    {
                        "stock_id": stock_id,
                        "signal_date": signal_date,
                        "date": trading_date,
                        "action": "BUY",
                        "price": price,
                        "quantity": quantity,
                        **transaction,
                        "cash_after": cash,
                    }
                )

        # 當日收盤估值。
        market_value = sum(
            (
                prices_by_stock[stock_id][trading_date]["close"]
                * shares
                for stock_id, shares in holdings.items()
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

        # 產生下一行情日的計畫。
        # 如果今日無法完整評估股票池，就不建立新計畫。
        pending = None

        if day["errors"]:
            skipped_rebalances.append(
                {
                    "date": trading_date,
                    "errors": day["errors"].copy(),
                }
            )
        else:
            ranked_stock_ids = [
                candidate["stock_id"]
                for candidate in day["candidates"]
            ]

            pending = {
                "signal_date": trading_date,
                "plan": plan_rebalance(
                    holdings,
                    ranked_stock_ids,
                    max_positions=max_positions,
                ),
            }

    final_equity = equity_curve[-1]["equity"]

    return {
        "initial_cash": initial_cash,
        "cash": cash,
        "holdings": holdings.copy(),
        "final_equity": final_equity,
        "total_return": final_equity / initial_cash - Decimal("1"),
        "total_commission": total_commission,
        "total_tax": total_tax,
        "trades": trades,
        "equity_curve": equity_curve,
        "skipped_orders": skipped_orders,
        "skipped_rebalances": skipped_rebalances,
    }