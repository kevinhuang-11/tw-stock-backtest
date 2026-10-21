import argparse
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tw_stock_backtest.backtesting.benchmark import (
    run_buy_and_hold,
)
from tw_stock_backtest.backtesting.corporate_actions import declined_text, dilution_total
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.metrics import (
    summarize_exposure,
    summarize_performance,
)
from tw_stock_backtest.backtesting.portfolio import (
    run_portfolio_backtest,
)
from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.data.restrictions import (
    annotate_backtest,
    coverage_summary,
    load_restrictions,
)
from tw_stock_backtest.data.universe import listing_exclusions
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.prices import PriceSource, describe_prices
from tw_stock_backtest.reporting import export_portfolio_report


def parse_arguments():
    parser = argparse.ArgumentParser(description="依每日選股排名，執行共用資金的多股票回測")

    parser.add_argument("--strategy", help="策略 JSON 檔；--top 優先，--ranking 不可與策略模式衝突")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置",
    )
    parser.add_argument(
        "--stocks",
        nargs="+",
        default=None,
        help="股票池；未指定時使用設定檔",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="回測開始日期，例如 2026-08-01",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="回測結束日期，例如 2026-09-22",
    )
    parser.add_argument(
        "--cash",
        default=None,
        help="初始資金",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        default=None,
        help="每次買進股數",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=None,
        help="最多持有幾檔股票",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="篩選後最多保留幾檔候選股",
    )
    parser.add_argument(
        "--show-equity",
        action="store_true",
        help="顯示每日現金、市值與總資產",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="匯出設定、行情快照、成交紀錄與資產報表",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="匯出目錄；搭配 --export，預設為專案的 reports",
    )
    parser.add_argument(
        "--ranking",
        choices=("rules", "factors"),
        default=None,
        help="排名方式：rules 為規則篩選，factors 為多因子評分",
    )
    parser.add_argument(
        "--sizing",
        choices=("fixed_quantity", "fixed_budget"),
        default=None,
        help="股數配置模式；未指定時使用設定檔",
    )
    parser.add_argument(
        "--slippage",
        default=None,
        help="單邊不利滑價率，例如 0.001 代表 0.1%%；未指定時使用設定檔",
    )
    return parser.parse_args()


def position_text(position):
    """期末持股一行；不是正常狀態時附上狀態、估值價與日期（docs/HALT-POLICY.md 3.14）。"""
    text = f"{position['stock_id']}：{position['shares']} 股"
    if position["status"] != "NORMAL":
        text += (
            f"｜{position['status_label']}，以 {position['price_date']} 收盤 {position['price']}"
            f" 估值，金額 {position['value']}"
        )
    notice = position["delisting_announced"]
    if notice and position["status"] != "UNREALIZABLE":
        text += f"｜已於 {notice['announced']} 公告 {notice['effective']} 終止上市"
    return text


def _load_settings(args):
    """設定檔加上 CLI 覆寫與策略；回傳 (settings, strategy)，並把 args.ranking 定為實際模式。"""
    settings = apply_overrides(
        load_config(args.config),
        {
            "universe": {
                "stocks": args.stocks,
            },
            "screening": {
                "top_n": (args.top if args.ranking == "rules" else None),
            },
            "factors": {
                "top_n": (args.top if args.ranking == "factors" else None),
            },
            "backtest": {
                "initial_cash": args.cash,
                "quantity": args.quantity,
                "max_positions": args.max_positions,
                "sizing_mode": args.sizing,
                "slippage_rate": args.slippage,
            },
        },
    )

    strategy = None
    if args.strategy:
        from tw_stock_backtest.strategies import effective_settings, load_strategy

        strategy = load_strategy(args.strategy)
        if args.ranking is not None and args.ranking != strategy["ranking_mode"]:
            raise ValueError("--ranking 與策略排名模式衝突")
        args.ranking = strategy["ranking_mode"]
        settings = effective_settings(settings, strategy, args.top)
        if args.top is not None:
            strategy["factors"]["top_n"] = strategy["screening"]["top_n"] = args.top
    args.ranking = args.ranking or "rules"
    return settings, strategy


def _run(args):
    """讀設定、排除股票、載入行情，執行策略與買進持有基準；失敗時丟出例外，由 main 處理。"""
    start, end = parse_date_range(args.start, args.end)
    settings, strategy = _load_settings(args)
    stocks = settings["universe"]["stocks"]
    screening_settings = settings["screening"]
    backtest_settings = settings["backtest"]
    db_path = settings["storage"]["database_path"]
    cost_settings = CostSettings(**settings["costs"])

    # 開始日還沒上市的股票不納入股票池（原因列在輸出與報表）。
    listing_excluded = listing_exclusions(stocks, db_path, start)
    excluded_ids = {item["stock_id"] for item in listing_excluded}
    stocks = [stock_id for stock_id in stocks if stock_id not in excluded_ids]
    if not stocks:
        raise ValueError("股票池在回測開始日都還沒上市")

    # 載入開始日前的資料，供篩選指標暖機。
    # 資金與持股仍從回測開始日才開始模擬。
    records_by_stock = {
        stock_id: load_records(
            stock_id,
            "1900-01-01",
            end.isoformat(),
            db_path=db_path,
        )
        for stock_id in stocks
    }

    # 訊號依價格模式計算；成交與估值用原始價格，持股與現金依公司行動調整（raw 模式不調整）。
    prices = PriceSource(settings)
    corporate_actions = prices.actions_by_stock(records_by_stock, end.isoformat())
    result = run_portfolio_backtest(
        records_by_stock,
        start.isoformat(),
        end.isoformat(),
        screening_settings=screening_settings,
        initial_cash=backtest_settings["initial_cash"],
        quantity=backtest_settings["quantity"],
        max_positions=backtest_settings["max_positions"],
        cost_settings=cost_settings,
        ranking_method=args.ranking,
        strategy=strategy,
        factor_settings=settings["factors"],
        sizing_mode=backtest_settings["sizing_mode"],
        slippage_rate=backtest_settings["slippage_rate"],
        corporate_actions=corporate_actions,
        delisted_valuation=settings["halts"]["delisted_valuation"],
    )
    result["price_adjustment"] = prices.describe()
    result["universe_exclusions"] = listing_excluded
    performance = summarize_performance(
        result["equity_curve"],
        result["initial_cash"],
    )
    performance.update(summarize_exposure(result["equity_curve"]))
    benchmark = run_buy_and_hold(
        records_by_stock,
        start.isoformat(),
        end.isoformat(),
        initial_cash=backtest_settings["initial_cash"],
        cost_settings=cost_settings,
        slippage_rate=backtest_settings["slippage_rate"],
        corporate_actions=corporate_actions,
        delisted_valuation=settings["halts"]["delisted_valuation"],
    )

    # 處置與全額交割：只標示成交與期末持股，不改變任何數字（docs/HALT-POLICY.md 3.17）。
    restrictions = load_restrictions(db_path)
    for data in (result, benchmark):
        annotate_backtest(data, restrictions)
    result["restriction_coverage"] = coverage_summary(db_path)

    benchmark_performance = summarize_performance(
        benchmark["equity_curve"],
        benchmark["initial_cash"],
    )
    benchmark_performance.update(summarize_exposure(benchmark["equity_curve"]))

    return SimpleNamespace(
        start=start,
        end=end,
        settings=settings,
        strategy=strategy,
        stocks=stocks,
        screening_settings=screening_settings,
        backtest_settings=backtest_settings,
        db_path=db_path,
        cost_settings=cost_settings,
        listing_excluded=listing_excluded,
        records_by_stock=records_by_stock,
        result=result,
        performance=performance,
        benchmark=benchmark,
        benchmark_performance=benchmark_performance,
    )


def _print_overview(run):
    """期間、股票池、價格口徑與公司行動。"""
    start, end, stocks, db_path, listing_excluded, result, benchmark = (
        run.start,
        run.end,
        run.stocks,
        run.db_path,
        run.listing_excluded,
        run.result,
        run.benchmark,
    )
    first_date = result["equity_curve"][0]["date"]
    last_date = result["equity_curve"][-1]["date"]

    print("多股票回測：每日排名，下一行情日開盤調整持股")
    print("含設定費稅與固定比例滑價；未計股利、拆股與精確零股成交")
    print("期末持股按收盤價估值，不強制賣出")
    print(f"\n股票池：{', '.join(stocks)}")
    for item in listing_excluded:
        print(f"未納入股票池：{item['stock_id']}（{item['reason']}）")
    print(f"資料庫位置：{db_path}")
    print(f"指定期間：{start} ～ {end}")
    print(f"實際期間：{first_date} ～ {last_date}")
    print(f"回測行情日數：{len(result['equity_curve'])}")
    print(describe_prices(result["price_adjustment"], backtest=True))
    for event in result["corporate_action_events"]:
        print(
            f"公司行動 {event['applied_on']} | {event['stock_id']} | 持股 {event['shares_before']}"
            f" → {event['shares_after']} | 入帳 {event['cash']} 元{declined_text(event)}"
        )
    if result.get("price_adjustment", {}).get("rights") == "decline":
        print(
            "放棄認購的稀釋損失合計（已反映在市價）："
            f"策略 {dilution_total(result['corporate_action_events'])} 元"
            f" | 基準 {dilution_total(benchmark['corporate_action_events'])} 元"
        )


def _print_settings(args, run):
    """排名模式、配置模式、費稅與滑價。"""
    settings, screening_settings, backtest_settings, cost_settings, result = (
        run.settings,
        run.screening_settings,
        run.backtest_settings,
        run.cost_settings,
        run.result,
    )
    if args.ranking == "rules":
        print("\n排名模式：規則篩選")
        print(
            f"趨勢條件：Close"
            f" > SMA{screening_settings['short_window']}"
            f" > SMA{screening_settings['long_window']}"
        )
        print(
            f"量比條件：當日量 / 前"
            f" {screening_settings['volume_window']} 筆均量"
            f" > {screening_settings['min_volume_ratio']}"
        )
        print(f"排名依據：{screening_settings['momentum_window']} 期價格報酬率")
        print(f"保留候選數：{screening_settings['top_n']}")

    else:
        factor_settings = settings["factors"]

        print("\n排名模式：多因子評分")
        print(
            f"期間：動能 {factor_settings['momentum_window']}，"
            f"均線 {factor_settings['short_window']}"
            f"/{factor_settings['long_window']}，"
            f"波動 {factor_settings['volatility_window']}"
        )
        print(
            f"權重：動能 {factor_settings['momentum_weight']}，"
            f"趨勢 {factor_settings['trend_weight']}，"
            f"低波動 {factor_settings['volatility_weight']}"
        )
        print(f"保留候選數：{factor_settings['top_n']}")
        print("不設定最低分數門檻，依排名選取目標持股。")
    print(f"持股上限：{backtest_settings['max_positions']}")
    if result["sizing_mode"] == "fixed_quantity":
        print("配置模式：固定股數")
        print(f"每次買進股數：{backtest_settings['quantity']}")
    else:
        print("配置模式：固定預算")
        print(f"單檔買進預算上限：{result['budget_per_position']:.2f} 元（含手續費）")
        print("預算按初始資金 / 持股上限計算，期間內維持不變")
        print("現金不足時縮減買進股數；既有持股不每日調整股數")
    print(f"\n手續費率：{cost_settings.commission_rate:.4%}")
    print(f"折扣倍率：{cost_settings.commission_discount}")
    print(f"最低手續費：{cost_settings.minimum_commission} 元")
    print(f"賣出交易稅率：{cost_settings.sell_tax_rate:.2%}")
    print(f"手續費取整：{cost_settings.commission_rounding}")
    print(f"交易稅取整：{cost_settings.tax_rounding}")
    print(f"單邊不利滑價率：{backtest_settings['slippage_rate']:.4%}")
    print("策略與基準採相同滑價率；持股估值仍使用原始收盤價")


def _print_performance(run):
    """資產、績效統計與買進持有基準的比較。"""
    result, performance, benchmark, benchmark_performance = (
        run.result,
        run.performance,
        run.benchmark,
        run.benchmark_performance,
    )
    print(f"\n初始資金：{result['initial_cash']:.2f}")
    print(f"期末現金：{result['cash']:.2f}")
    print(f"期末總資產：{result['final_equity']:.2f}")
    print(f"區間報酬率：{result['total_return']:.4%}")
    print(f"累計手續費：{result['total_commission']:.2f}")
    print(f"累計交易稅：{result['total_tax']:.2f}")
    print(f"成交筆數：{len(result['trades'])}")
    print("\n績效統計：")
    print(f"最大回撤（每日收盤）：{performance['max_drawdown']:.4%}")
    print(f"最高收盤總資產：{performance['highest_equity']:.2f}（{performance['highest_date']}）")
    print(f"最低收盤總資產：{performance['lowest_equity']:.2f}（{performance['lowest_date']}）")
    print(f"持股天數：{performance['invested_days']}")
    print(f"空手天數：{performance['cash_only_days']}")
    print(f"持股天數占比：{performance['invested_day_ratio']:.2%}")
    print(f"平均持股市值占比（含空手日）：{performance['average_exposure']:.2%}")
    print(f"最高持股市值占比：{performance['max_exposure']:.2%}")
    print(f"基準平均持股市值占比：{benchmark_performance['average_exposure']:.2%}")
    print(f"基準最高持股市值占比：{benchmark_performance['max_exposure']:.2%}")
    print("\n買進持有基準比較：")
    print("基準：每檔在期間內第一個可交易日，以等預算買進")
    print("基準股數依預算計算，不使用策略的固定股數或持股上限")
    print(f"策略期末總資產：{result['final_equity']:.2f} | 基準：{benchmark['final_equity']:.2f}")
    print(
        f"策略報酬率：{performance['total_return']:.4%}"
        f" | 基準：{benchmark_performance['total_return']:.4%}"
    )
    print(
        f"策略最大回撤：{performance['max_drawdown']:.4%}"
        f" | 基準：{benchmark_performance['max_drawdown']:.4%}"
    )

    return_difference = performance["total_return"] - benchmark_performance["total_return"]
    print(f"報酬率差（策略－基準）：{return_difference * 100:+.4f} 個百分點")

    print(
        f"策略累計費稅："
        f"{result['total_commission'] + result['total_tax']:.2f}"
        f" | 基準："
        f"{benchmark['total_commission'] + benchmark['total_tax']:.2f}"
    )

    print("基準持股：")
    for position in benchmark["final_positions"]:
        print(f"  {position_text(position)}")

    if benchmark["unbought"]:
        print("基準因單檔預算不足而未買進：" + ", ".join(benchmark["unbought"]))
    if benchmark["unavailable_entire_period"]:
        print(
            "基準因整段期間皆無法交易而未買進：" + ", ".join(benchmark["unavailable_entire_period"])
        )


def _print_records(run):
    """期末持股、成交、未成交、不可交易日與未更新訊號的日期。"""
    result, benchmark = (run.result, run.benchmark)
    print("\n期末持股：")

    if not result["final_positions"]:
        print("無")
    else:
        for position in result["final_positions"]:
            print(position_text(position))

    print("\n成交紀錄：")

    if not result["trades"]:
        print("本區間沒有成交。")
    else:
        for trade in result["trades"]:
            print(
                f"訊號日 {trade['signal_date']}"
                f" → 成交日 {trade['date']}"
                f" | {trade['stock_id']} {trade['action']}"
                f" | 價格 {trade['price']:.2f}"
                f" | 股數 {trade['quantity']}"
                f" | 手續費 {trade['commission']}"
                f" | 交易稅 {trade['tax']}"
                f" | 剩餘現金 {trade['cash_after']:.2f}"
            )

    print(f"\n未成交交易計畫：{len(result['skipped_orders'])} 筆")

    for order in result["skipped_orders"]:
        print(
            f"{order['date']} | {order['stock_id']}"
            f" | {order['action']}"
            f" | 訊號日 {order['signal_date']}"
            f" | {order['reason']}"
        )

    print(
        f"\n停牌、無成交價與缺列日：{len(result['market_events'])} 筆"
        "（當日不成交，持股沿用最近收盤估值）"
    )

    for event in result["market_events"]:
        print(f"{event['date']} | {event['stock_id']} | {event['reason']}")

    gaps = result["unconfirmed_gaps"]
    if gaps:
        print(
            f"\n原因未確認的缺列：{len(gaps)} 檔（視為不可交易、沿用最近收盤估值；"
            "結果不完整，退出碼 2）"
        )
        for gap in gaps:
            print(f"{gap['stock_id']} | {gap['first']} ～ {gap['last']} | {gap['days']} 個行情日")

    for source, data in (("策略", result), ("基準", benchmark)):
        for item in data["unrealizable"].values():
            value = (
                "以 0 估值（壓力測試）"
                if data["delisted_valuation"] == "zero"
                else f"以 {item['price_date']} 收盤 {item['last_close']} 估值"
            )
            print(
                f"\n{source}無法變現：{item['stock_id']} {item['shares']} 股，"
                f"{item['effective']} 終止上市（{item['kind_label']}：{item['reason']}），{value}"
            )

    print(f"\n有個別股票未更新訊號的日期：{len(result['skipped_rebalances'])} 日")

    for item in result["skipped_rebalances"]:
        reasons = "；".join(
            f"{stock_id}：{message}" for stock_id, message in item["errors"].items()
        )
        print(f"{item['date']} | {reasons}")


def _print_equity(run):
    """每日資產（--show-equity）。"""
    result = run.result
    print("\n每日資產：")
    print("日期 | 現金 | 持股市值 | 總資產 | 持股")

    for point in result["equity_curve"]:
        holdings_text = (
            ", ".join(
                f"{stock_id}:{quantity}" for stock_id, quantity in sorted(point["holdings"].items())
            )
            or "無"
        )

        print(
            f"{point['date']}"
            f" | {point['cash']:.2f}"
            f" | {point['market_value']:.2f}"
            f" | {point['equity']:.2f}"
            f" | {holdings_text}"
        )


def _export(args, run):
    """匯出報表；失敗時回傳退出碼 1，成功時回傳 None。"""
    (
        start,
        end,
        settings,
        strategy,
        records_by_stock,
        result,
        performance,
        benchmark,
        benchmark_performance,
    ) = (
        run.start,
        run.end,
        run.settings,
        run.strategy,
        run.records_by_stock,
        run.result,
        run.performance,
        run.benchmark,
        run.benchmark_performance,
    )
    output_root = (
        Path(args.output_dir)
        if args.output_dir is not None
        else DEFAULT_CONFIG_PATH.parent / "reports"
    )

    try:
        report_dir = export_portfolio_report(
            output_root,
            settings=settings,
            strategy=strategy,
            start_text=start.isoformat(),
            end_text=end.isoformat(),
            records_by_stock=records_by_stock,
            result=result,
            performance=performance,
            benchmark=benchmark,
            benchmark_performance=benchmark_performance,
        )
    except (ValueError, TypeError, OSError) as error:
        print(f"\n回測已完成，但報表匯出失敗：{error}")
        return 1

    print(f"\n報表已匯出：{report_dir}")


def main():
    args = parse_arguments()

    try:
        run = _run(args)
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"多股票回測失敗：{error}")
        return 1

    _print_overview(run)
    _print_settings(args, run)
    _print_performance(run)
    _print_records(run)
    if args.show_equity:
        _print_equity(run)
    if args.export:
        code = _export(args, run)
        if code is not None:
            return code

    # 有原因未確認的缺列時結果不完整：沿用研究類 CLI 的約定，退出碼 2。
    return 2 if run.result["unconfirmed_gaps"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
