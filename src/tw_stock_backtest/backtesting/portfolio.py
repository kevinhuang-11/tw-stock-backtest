from dataclasses import dataclass, field
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
from tw_stock_backtest.backtesting.delisting import (
    check_valuation_mode,
    retire_delisted,
    unrealizable_value,
)
from tw_stock_backtest.backtesting.positions import final_positions
from tw_stock_backtest.data.market_events import (
    CONFIRMED_DELISTINGS,
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


@dataclass
class _Ranker:
    """排名設定：rules（規則篩選）、factors（多因子）或策略庫的策略。"""

    method: str
    top_n: int
    rule_parameters: dict | None = None
    factor_settings: dict | None = None
    weights: dict | None = None
    strategy: dict | None = None


@dataclass
class _OrderRules:
    """開盤成交用的固定參數。"""

    quantity: int
    max_positions: int
    sizing_mode: str
    budget_per_position: Decimal
    slippage_rate: Decimal
    cost_settings: CostSettings


@dataclass
class _Account:
    """回測過程中會變動的帳戶狀態與紀錄。"""

    cash: Decimal
    holdings: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    skipped_orders: list = field(default_factory=list)
    total_commission: Decimal = Decimal("0")
    total_tax: Decimal = Decimal("0")
    # 賣出意圖（docs/HALT-POLICY.md 3.15）：{股票: 最初決定賣出的訊號日}。賣出因不可交易
    # 而沒成交時保留，之後每天都重新計畫賣出，不因指標暫時無法評估而凍結；重新評估後又
    # 進入目標持股時取消。
    sell_intents: dict = field(default_factory=dict)


def _strategy_settings(strategy, ranking_method, factor_settings, screening_settings):
    """有策略時，排名模式與參數都改用策略的設定。"""
    if strategy is None:
        return None, ranking_method, factor_settings, screening_settings

    from tw_stock_backtest.strategies import validate_strategy

    strategy = validate_strategy(strategy)
    factor_settings = {
        k: Decimal(v) if k.endswith("_weight") else v for k, v in strategy["factors"].items()
    }
    screening_settings = {
        k: Decimal(v) if k == "min_volume_ratio" else v for k, v in strategy["screening"].items()
    }
    return strategy, strategy["ranking_mode"], factor_settings, screening_settings


def _validate_inputs(initial_cash, quantity, cost_settings, slippage_rate, max_positions, sizing):
    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash <= 0:
        raise ValueError("初始資金必須是正值且有限的 Decimal")

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("買進股數必須是正整數")

    if not isinstance(cost_settings, CostSettings):
        raise ValueError("必須提供 CostSettings")
    # 提前驗證滑價率，即使本區間沒有成交也要檢查。
    apply_slippage(Decimal("1"), "BUY", slippage_rate)

    plan_rebalance({}, [], max_positions=max_positions)
    if sizing not in ("fixed_quantity", "fixed_budget"):
        raise ValueError("股數配置模式必須是 fixed_quantity 或 fixed_budget")


def _ranker(ranking_method, screening_settings, factor_settings, strategy):
    if ranking_method == "rules":
        active_settings = screening_settings
        ranker = _Ranker(
            method="rules",
            top_n=0,
            rule_parameters={
                key: screening_settings[key]
                for key in (
                    "short_window",
                    "long_window",
                    "volume_window",
                    "min_volume_ratio",
                    "momentum_window",
                )
            },
        )
    elif ranking_method == "factors" and factor_settings is not None:
        active_settings = factor_settings
        weights = {
            "momentum": factor_settings["momentum_weight"],
            "trend": factor_settings["trend_weight"],
            "volatility": factor_settings["volatility_weight"],
        }
        rank_factor_candidates([], weights=weights)
        ranker = _Ranker(
            method="factors", top_n=0, factor_settings=factor_settings, weights=weights
        )
    else:
        raise ValueError("排名模式或因子設定無效")

    top_n = active_settings["top_n"]
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise ValueError("top_n 必須是正整數")
    ranker.top_n = top_n
    ranker.strategy = strategy
    return ranker


def _warm_histories(market, actions, start):
    """暖機資料保留停牌與無成交價的列，與研究排名使用同一套規則：
    窗口內有缺價就不計算，不刪列湊窗口。

    訊號用的序列逐日累積：每個訊號日只套用該日以前生效的公司行動（actions 為
    {股票: 公司行動}；空字典時為原始價格）。成交與估值使用 market 中的原始價格；
    持股與現金在生效日開盤前依公司行動調整（見 backtesting/corporate_actions.py）。
    """
    return {
        stock_id: AdjustedHistory(actions.get(stock_id, ())).extend(
            record
            for record_date, record in prices.items()
            if date.fromisoformat(record_date) < start
        )
        for stock_id, prices in market.items()
    }


def _skip(account, stock_id, signal_date, trading_date, action, reason):
    account.skipped_orders.append(
        {
            "stock_id": stock_id,
            "signal_date": signal_date,
            "date": trading_date,
            "action": action,
            "reason": reason,
        }
    )


def _execute_order(account, rules, action, stock_id, record, signal_date, trading_date):
    """開盤成交一筆；不能成交時記在 skipped_orders 並說明原因。"""
    if action == "SELL" and stock_id not in account.holdings:
        # 計畫賣出後、成交前終止上市：持股已轉為無法變現。
        _skip(
            account,
            stock_id,
            signal_date,
            trading_date,
            action,
            "已終止上市，持股已轉為無法變現",
        )
        return

    reason = None
    transaction = None
    shares = account.holdings[stock_id] if action == "SELL" else rules.quantity

    if not record["tradable"]:
        reason = f"{not_tradable_reason(record)}，當日不成交"

    elif action == "BUY" and len(account.holdings) >= rules.max_positions:
        reason = "已達持股上限"

    else:
        execution_price = apply_slippage(record["open"], action, rules.slippage_rate)

        # 只有固定預算買進，需要重新計算股數。
        if action == "BUY" and rules.sizing_mode == "fixed_budget":
            available_budget = min(rules.budget_per_position, account.cash)
            shares = calculate_buy_quantity(
                execution_price,
                available_budget,
                rules.cost_settings,
            )
            if shares == 0:
                reason = "預算或現金不足以買進一股，含手續費"

        # 所有可成交的買進與賣出，都要計算交易金額。
        if reason is None:
            transaction = calculate_transaction(
                execution_price,
                shares,
                action,
                rules.cost_settings,
            )
            if action == "BUY" and account.cash < -transaction["cash_change"]:
                reason = "資金不足，含手續費"

    if reason is not None:
        _skip(account, stock_id, signal_date, trading_date, action, reason)
        if action == "SELL" and not record["tradable"]:
            account.sell_intents.setdefault(stock_id, signal_date)
        return

    account.cash += transaction["cash_change"]

    if action == "SELL":
        del account.holdings[stock_id]
    else:
        account.holdings[stock_id] = shares

    account.total_commission += transaction["commission"]
    account.total_tax += transaction["tax"]

    account.trades.append(
        {
            "stock_id": stock_id,
            "signal_date": signal_date,
            "date": trading_date,
            "action": action,
            "raw_price": record["open"],
            "price": execution_price,
            "quantity": shares,
            **transaction,
            "cash_after": account.cash,
            # 停牌後重試的賣出：記錄最初決定賣出的訊號日。
            **(
                {"intent_date": account.sell_intents.pop(stock_id)}
                if action == "SELL" and stock_id in account.sell_intents
                else {}
            ),
        }
    )


def _execute_plan(account, rules, pending, market, trading_date):
    """開盤：執行上一行情日收盤後產生的計畫，先賣後買。"""
    signal_date, plan = pending
    for action, stock_ids in (("SELL", plan["sell"]), ("BUY", plan["buy"])):
        for stock_id in stock_ids:
            _execute_order(
                account,
                rules,
                action,
                stock_id,
                market[stock_id][trading_date],
                signal_date,
                trading_date,
            )


def _close_valuation(account, market, trading_date, unrealizable, delisted_valuation):
    """收盤：持有中的停牌或無成交價股票使用最近有效收盤價估值，並標示價格日期。"""
    market_value = Decimal("0")
    stale_prices = {}

    for stock_id, shares in account.holdings.items():
        record = market[stock_id][trading_date]
        valuation = record["valuation_close"]

        if valuation is None:
            raise ValueError(f"{stock_id} 沒有可用估值價格")

        market_value += valuation * shares

        if not record["tradable"]:
            stale_prices[stock_id] = record["valuation_price_date"]

    frozen_value = unrealizable_value(unrealizable, delisted_valuation)
    return {
        "date": trading_date,
        "cash": account.cash,
        "market_value": market_value,
        "unrealizable_value": frozen_value,
        "equity": account.cash + market_value + frozen_value,
        "holdings": account.holdings.copy(),
        "stale_prices": stale_prices,
    }


def _evaluate_day(ranker, market, histories, trading_date, events):
    """逐檔計算訊號並排名，回傳 (候選, 無法更新的原因, 當天的不可交易事件, 策略診斷)。

    一檔停牌，不阻止其他股票更新訊號。
    """
    evaluations = []
    errors = {}
    day_events = []

    for stock_id, prices in market.items():
        record = prices[trading_date]

        histories[stock_id].append(record)

        if record["market_status"] == "DELISTED":
            # 已終止上市：不再評估，也不逐日記錄（終止上市本身另列在 delisting_events）。
            continue

        if not record["tradable"]:
            errors[stock_id] = f"{not_tradable_reason(record)}，僅暫停本股票訊號更新"
            day_events.append(
                {
                    "stock_id": stock_id,
                    "date": trading_date,
                    **market_event(record, events),
                }
            )
            continue

        try:
            if ranker.method == "rules":
                evaluation = evaluate_stock(
                    histories[stock_id].rows,
                    trading_date,
                    **ranker.rule_parameters,
                )
            else:
                evaluation = calculate_factor_row(
                    histories[stock_id].rows,
                    trading_date,
                    factor_settings=ranker.factor_settings,
                )

            evaluations.append(evaluation)

        except ValueError as error:
            errors[stock_id] = str(error)

    if ranker.method == "rules":
        candidates = [row for row in evaluations if row["selected"]]
        candidates.sort(key=lambda row: (-row["momentum"], row["stock_id"]))
    else:
        candidates = rank_factor_candidates(evaluations, weights=ranker.weights)

    diagnostics = None
    if ranker.strategy is not None:
        from tw_stock_backtest.strategies import analyze_strategy

        series = {stock_id: history.rows for stock_id, history in histories.items()}
        analysis = analyze_strategy(series, trading_date, ranker.strategy, indicator_records=series)
        candidates = analysis["candidates"]
        errors.update(
            (stock_id, message)
            for stock_id, message in analysis["errors"].items()
            if market[stock_id][trading_date]["market_status"] != "DELISTED"
        )
        diagnostics = {"date": trading_date, **analysis}

    return candidates, errors, day_events, diagnostics


def _plan_next(account, candidates, errors, *, top_n, max_positions):
    """收盤後決定下一個開盤的計畫。

    停牌或無法更新訊號的既有持股暫時保留，仍占名額；已決定賣出的不凍結，繼續計畫賣出。
    """
    holdings, sell_intents = account.holdings, account.sell_intents
    frozen = sorted(
        stock_id for stock_id in holdings if stock_id in errors and stock_id not in sell_intents
    )

    available_slots = max_positions - len(frozen)

    ranked_ids = [row["stock_id"] for row in candidates[:top_n] if row["stock_id"] not in frozen]

    target_ids = frozen + ranked_ids[:available_slots]

    # 重新評估後又進入目標的持股，不再賣出。
    for stock_id in [s for s in sell_intents if s in target_ids]:
        del sell_intents[stock_id]

    return plan_rebalance(holdings, target_ids, max_positions=max_positions)


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
    delistings=None,
    delisted_valuation="last_close",
):
    """多股票回測：每天收盤後排名，下一個行情日開盤依計畫調整持股。

    流程分成：設定與驗證、整理行情與暖機序列，以及每個行情日的四步——
    公司行動與終止上市、開盤成交（_execute_plan）、收盤估值（_close_valuation）、
    逐檔評估與排名（_evaluate_day）、決定下一個開盤的計畫（_plan_next）。
    """
    strategy, ranking_method, factor_settings, screening_settings = _strategy_settings(
        strategy, ranking_method, factor_settings, screening_settings
    )
    start, end = parse_date_range(start_text, end_text)
    _validate_inputs(
        initial_cash, quantity, cost_settings, slippage_rate, max_positions, sizing_mode
    )

    # 整段回測使用相同的單檔買進預算上限。
    # 此版本不隨資產變化調整預算，也不調整既有持股股數。
    rules = _OrderRules(
        quantity=quantity,
        max_positions=max_positions,
        sizing_mode=sizing_mode,
        budget_per_position=initial_cash / Decimal(max_positions),
        slippage_rate=slippage_rate,
        cost_settings=cost_settings,
    )
    ranker = _ranker(ranking_method, screening_settings, factor_settings, strategy)

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
    actions = corporate_actions or {}
    histories = _warm_histories(market, actions, start)

    account = _Account(cash=initial_cash)
    skipped_rebalances = []
    equity_curve = []
    market_events = []
    strategy_diagnostics = []
    pending = None

    # 公司行動帳：生效日開盤前，依前一日收盤的持股入帳股利、退還股款並調整股數。
    schedule = Schedule(actions, "")
    corporate_action_events = []

    # 終止上市的持股（docs/HALT-POLICY.md 3.16）：不再占名額，估值依 delisted_valuation。
    unrealizable = {}
    delisting_events = []

    for trading_date in trading_dates:
        received, applied = apply_due(schedule, trading_date, account.holdings)
        account.cash += received
        corporate_action_events.extend(applied)
        retired = retire_delisted(account.holdings, unrealizable, market, trading_date, delistings)
        delisting_events.extend(retired)
        for event in retired:
            account.sell_intents.pop(event["stock_id"], None)

        if pending is not None:
            _execute_plan(account, rules, pending, market, trading_date)

        equity_curve.append(
            _close_valuation(account, market, trading_date, unrealizable, delisted_valuation)
        )

        candidates, errors, day_events, diagnostics = _evaluate_day(
            ranker, market, histories, trading_date, events
        )
        market_events.extend(day_events)
        if diagnostics is not None:
            strategy_diagnostics.append(diagnostics)

        pending = (
            trading_date,
            _plan_next(
                account, candidates, errors, top_n=ranker.top_n, max_positions=max_positions
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

    if strategy is not None:
        from tw_stock_backtest.strategies import snapshot

    return {
        **(
            {"strategy_snapshot": snapshot(strategy), "strategy_diagnostics": strategy_diagnostics}
            if strategy is not None
            else {}
        ),
        "ranking_method": ranking_method,
        "sizing_mode": sizing_mode,
        "budget_per_position": (
            rules.budget_per_position if sizing_mode == "fixed_budget" else None
        ),
        "initial_cash": initial_cash,
        "cash": account.cash,
        "holdings": account.holdings.copy(),
        "final_equity": final_equity,
        "total_return": final_equity / initial_cash - Decimal("1"),
        "total_commission": account.total_commission,
        "total_tax": account.total_tax,
        "trades": account.trades,
        "equity_curve": equity_curve,
        "skipped_orders": account.skipped_orders,
        "skipped_rebalances": skipped_rebalances,
        "market_events": market_events,
        # 原因未確認的缺列（MISSING_ROW）；有的話這次結果不完整（docs/HALT-POLICY.md 3.6）。
        "unconfirmed_gaps": unconfirmed_gaps(market),
        "unrealizable": unrealizable,
        "final_positions": final_positions(
            account.holdings,
            unrealizable,
            market,
            trading_dates[-1],
            delistings,
            delisted_valuation,
        ),
        "delisting_events": delisting_events,
        "delisted_valuation": delisted_valuation,
        "corporate_action_events": corporate_action_events,
        "slippage_rate": slippage_rate,
    }
