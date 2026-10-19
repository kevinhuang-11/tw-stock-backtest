from datetime import date
from decimal import Decimal

from tw_stock_backtest.analysis.adjustment import AdjustedHistory
from tw_stock_backtest.analysis.factors import (
    calculate_factor_row,
    rank_factor_candidates,
)
from tw_stock_backtest.analysis.screening import evaluate_stock
from tw_stock_backtest.backtesting.corporate_actions import Schedule, apply_due
from tw_stock_backtest.backtesting.costs import (
    CostSettings,
    apply_slippage,
    calculate_buy_quantity,
    calculate_transaction,
)
from tw_stock_backtest.data.market_events import (
    CONFIRMED_HALTS,
    market_event,
    not_tradable_reason,
    prepare_backtest_market,
    unconfirmed_gaps,
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
    if isinstance(max_positions, bool) or not isinstance(max_positions, int) or max_positions <= 0:
        raise ValueError("max_positions 必須是正整數")

    if not isinstance(holdings, dict):
        raise ValueError("holdings 必須是字典")

    if not isinstance(ranked_stock_ids, (list, tuple)):
        raise ValueError("候選排名必須是清單或 tuple")

    for stock_id, quantity in holdings.items():
        if not isinstance(stock_id, str) or not stock_id or stock_id != stock_id.strip():
            raise ValueError("持股代號必須是非空且無前後空白的字串")

        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("持股股數必須是正整數")

    for stock_id in ranked_stock_ids:
        if not isinstance(stock_id, str) or not stock_id or stock_id != stock_id.strip():
            raise ValueError("候選代號必須是非空且無前後空白的字串")

    if len(set(ranked_stock_ids)) != len(ranked_stock_ids):
        raise ValueError("候選排名不可包含重複股票")

    target = list(ranked_stock_ids[:max_positions])
    target_set = set(target)
    held_set = set(holdings)

    # 賣出順序固定，方便重現與測試。
    sell = sorted(held_set - target_set)

    # 買進順序保留候選排名。
    buy = [stock_id for stock_id in target if stock_id not in held_set]

    keep = [stock_id for stock_id in target if stock_id in held_set]

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
    ranking_method="rules",
    factor_settings=None,
    confirmed_halts=None,
    sizing_mode="fixed_quantity",
    slippage_rate=Decimal("0"),
    strategy=None,
    corporate_actions=None,
):

    strategy_diagnostics = []
    if strategy is not None:
        from tw_stock_backtest.strategies import analyze_strategy, snapshot, validate_strategy

        strategy = validate_strategy(strategy)
        ranking_method = strategy["ranking_mode"]
        factor_settings = {
            k: Decimal(v) if k.endswith("_weight") else v for k, v in strategy["factors"].items()
        }
        screening_settings = {
            k: Decimal(v) if k == "min_volume_ratio" else v
            for k, v in strategy["screening"].items()
        }
    start, end = parse_date_range(start_text, end_text)

    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash <= 0:
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("買進股數必須是正整數")

    if not isinstance(cost_settings, CostSettings):
        raise ValueError("必須提供 CostSettings")
    # 提前驗證滑價率，即使本區間沒有成交也要檢查。
    apply_slippage(Decimal("1"), "BUY", slippage_rate)

    plan_rebalance({}, [], max_positions=max_positions)
    if sizing_mode not in ("fixed_quantity", "fixed_budget"):
        raise ValueError("股數配置模式必須是 fixed_quantity 或 fixed_budget")

    # 整段回測使用相同的單檔買進預算上限。
    # 此版本不隨資產變化調整預算，也不調整既有持股股數。
    budget_per_position = initial_cash / Decimal(max_positions)
    if ranking_method == "rules":
        active_settings = screening_settings
        rule_parameters = {
            key: screening_settings[key]
            for key in (
                "short_window",
                "long_window",
                "volume_window",
                "min_volume_ratio",
                "momentum_window",
            )
        }
    elif ranking_method == "factors" and factor_settings is not None:
        active_settings = factor_settings
        weights = {
            "momentum": factor_settings["momentum_weight"],
            "trend": factor_settings["trend_weight"],
            "volatility": factor_settings["volatility_weight"],
        }
        rank_factor_candidates([], weights=weights)
    else:
        raise ValueError("排名模式或因子設定無效")

    top_n = active_settings["top_n"]
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise ValueError("top_n 必須是正整數")

    events = CONFIRMED_HALTS if confirmed_halts is None else confirmed_halts

    market, trading_dates = prepare_backtest_market(
        records_by_stock,
        start,
        end,
        confirmed_halts=events,
    )

    # 暖機資料保留停牌與無成交價的列，與研究排名使用同一套規則：
    # 窗口內有缺價就不計算，不刪列湊窗口。
    # 訊號用的序列逐日累積：每個訊號日只套用該日以前生效的公司行動（corporate_actions 為
    # {股票: 公司行動}；省略時為原始價格）。成交與估值使用 market 中的原始價格；
    # 持股與現金在生效日開盤前依公司行動調整（見 backtesting/corporate_actions.py）。
    actions = corporate_actions or {}
    histories = {
        stock_id: AdjustedHistory(actions.get(stock_id, ())).extend(
            record
            for record_date, record in prices.items()
            if date.fromisoformat(record_date) < start
        )
        for stock_id, prices in market.items()
    }

    cash = initial_cash
    holdings = {}
    trades = []
    skipped_orders = []
    skipped_rebalances = []
    equity_curve = []
    market_events = []
    total_commission = Decimal("0")
    total_tax = Decimal("0")
    pending = None

    # 公司行動帳：生效日開盤前，依前一日收盤的持股入帳股利、退還股款並調整股數。
    schedule = Schedule(actions, "")
    corporate_action_events = []

    for trading_date in trading_dates:
        received, applied = apply_due(schedule, trading_date, holdings)
        cash += received
        corporate_action_events.extend(applied)

        # 開盤：執行上一行情日收盤後產生的計畫。
        if pending is not None:
            signal_date, plan = pending

            for action, stock_ids in (
                ("SELL", plan["sell"]),
                ("BUY", plan["buy"]),
            ):
                for stock_id in stock_ids:
                    record = market[stock_id][trading_date]
                    reason = None
                    transaction = None

                    shares = holdings[stock_id] if action == "SELL" else quantity

                    if not record["tradable"]:
                        reason = f"{not_tradable_reason(record)}，當日不成交"

                    elif action == "BUY" and len(holdings) >= max_positions:
                        reason = "已達持股上限"

                    else:
                        execution_price = apply_slippage(
                            record["open"],
                            action,
                            slippage_rate,
                        )

                        # 只有固定預算買進，需要重新計算股數。
                        if action == "BUY" and sizing_mode == "fixed_budget":
                            available_budget = min(
                                budget_per_position,
                                cash,
                            )

                            shares = calculate_buy_quantity(
                                execution_price,
                                available_budget,
                                cost_settings,
                            )

                            if shares == 0:
                                reason = "預算或現金不足以買進一股，含手續費"

                        # 所有可成交的買進與賣出，都要計算交易金額。
                        # 這個 if 與上方配置模式的 if 同一層。
                        if reason is None:
                            transaction = calculate_transaction(
                                execution_price,
                                shares,
                                action,
                                cost_settings,
                            )

                            if action == "BUY" and cash < -transaction["cash_change"]:
                                reason = "資金不足，含手續費"

                    if reason is not None:
                        skipped_orders.append(
                            {
                                "stock_id": stock_id,
                                "signal_date": signal_date,
                                "date": trading_date,
                                "action": action,
                                "reason": reason,
                            }
                        )
                        continue

                    cash += transaction["cash_change"]

                    if action == "SELL":
                        del holdings[stock_id]
                    else:
                        holdings[stock_id] = shares

                    total_commission += transaction["commission"]
                    total_tax += transaction["tax"]

                    trades.append(
                        {
                            "stock_id": stock_id,
                            "signal_date": signal_date,
                            "date": trading_date,
                            "action": action,
                            "raw_price": record["open"],
                            "price": execution_price,
                            "quantity": shares,
                            **transaction,
                            "cash_after": cash,
                        }
                    )

        # 收盤：持有中的停牌或無成交價股票使用最近有效收盤價估值，並標示價格日期。
        market_value = Decimal("0")
        stale_prices = {}

        for stock_id, shares in holdings.items():
            record = market[stock_id][trading_date]
            valuation = record["valuation_close"]

            if valuation is None:
                raise ValueError(f"{stock_id} 沒有可用估值價格")

            market_value += valuation * shares

            if not record["tradable"]:
                stale_prices[stock_id] = record["valuation_price_date"]

        equity_curve.append(
            {
                "date": trading_date,
                "cash": cash,
                "market_value": market_value,
                "equity": cash + market_value,
                "holdings": holdings.copy(),
                "stale_prices": stale_prices,
            }
        )

        # 逐檔計算：一檔停牌，不阻止其他股票更新訊號。
        evaluations = []
        errors = {}

        for stock_id, prices in market.items():
            record = prices[trading_date]

            histories[stock_id].append(record)

            if not record["tradable"]:
                errors[stock_id] = f"{not_tradable_reason(record)}，僅暫停本股票訊號更新"
                market_events.append(
                    {
                        "stock_id": stock_id,
                        "date": trading_date,
                        **market_event(record, events),
                    }
                )
                continue

            try:
                if ranking_method == "rules":
                    evaluation = evaluate_stock(
                        histories[stock_id].rows,
                        trading_date,
                        **rule_parameters,
                    )
                else:
                    evaluation = calculate_factor_row(
                        histories[stock_id].rows,
                        trading_date,
                        factor_settings=factor_settings,
                    )

                evaluations.append(evaluation)

            except ValueError as error:
                errors[stock_id] = str(error)

        if ranking_method == "rules":
            candidates = [row for row in evaluations if row["selected"]]
            candidates.sort(key=lambda row: (-row["momentum"], row["stock_id"]))
        else:
            candidates = rank_factor_candidates(
                evaluations,
                weights=weights,
            )

        if strategy is not None:
            series = {stock_id: history.rows for stock_id, history in histories.items()}
            analysis = analyze_strategy(series, trading_date, strategy, indicator_records=series)
            candidates = analysis["candidates"]
            errors.update(analysis["errors"])
            strategy_diagnostics.append({"date": trading_date, **analysis})

        # 停牌或無法更新訊號的既有持股暫時保留，仍占名額。
        frozen = sorted(stock_id for stock_id in holdings if stock_id in errors)

        available_slots = max_positions - len(frozen)

        ranked_ids = [
            row["stock_id"] for row in candidates[:top_n] if row["stock_id"] not in frozen
        ]

        target_ids = frozen + ranked_ids[:available_slots]

        pending = (
            trading_date,
            plan_rebalance(
                holdings,
                target_ids,
                max_positions=max_positions,
            ),
        )

        if errors:
            skipped_rebalances.append(
                {
                    "date": trading_date,
                    "errors": errors.copy(),
                }
            )

    final_equity = equity_curve[-1]["equity"]

    return {
        **(
            {"strategy_snapshot": snapshot(strategy), "strategy_diagnostics": strategy_diagnostics}
            if strategy is not None
            else {}
        ),
        "ranking_method": ranking_method,
        "sizing_mode": sizing_mode,
        "budget_per_position": (budget_per_position if sizing_mode == "fixed_budget" else None),
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
        "market_events": market_events,
        # 原因未確認的缺列（MISSING_ROW）；有的話這次結果不完整（docs/HALT-POLICY.md 3.6）。
        "unconfirmed_gaps": unconfirmed_gaps(market),
        "corporate_action_events": corporate_action_events,
        "slippage_rate": slippage_rate,
    }
