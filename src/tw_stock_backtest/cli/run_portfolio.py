import argparse
import sqlite3

from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import (
    run_portfolio_backtest,
)
from tw_stock_backtest.backtesting.metrics import (
    summarize_performance,
)
from tw_stock_backtest.backtesting.benchmark import (
    run_buy_and_hold,
)
from pathlib import Path
from tw_stock_backtest.reporting import export_portfolio_report



def parse_arguments():
    parser = argparse.ArgumentParser(
        description="依每日選股排名，執行共用資金的多股票回測"
    )

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
        default="rules",
        help="排名方式：rules 為規則篩選，factors 為多因子評分",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        start, end = parse_date_range(args.start, args.end)

        settings = apply_overrides(
            load_config(args.config),
            {
                "universe": {
                    "stocks": args.stocks,
                },
                "screening": {
                    "top_n": (
                        args.top
                        if args.ranking == "rules"
                        else None
                    ),
                },
                "factors": {
                    "top_n": (
                        args.top
                        if args.ranking == "factors"
                        else None
                    ),
                },
                "backtest": {
                    "initial_cash": args.cash,
                    "quantity": args.quantity,
                    "max_positions": args.max_positions,
                },
            },
        )

        stocks = settings["universe"]["stocks"]
        screening_settings = settings["screening"]
        backtest_settings = settings["backtest"]
        db_path = settings["storage"]["database_path"]
        cost_settings = CostSettings(**settings["costs"])

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
            factor_settings=settings["factors"],
        )
        performance = summarize_performance(
            result["equity_curve"],
            result["initial_cash"],
        )
        benchmark = run_buy_and_hold(
            records_by_stock,
            start.isoformat(),
            end.isoformat(),
            initial_cash=backtest_settings["initial_cash"],
            cost_settings=cost_settings,
        )

        benchmark_performance = summarize_performance(
            benchmark["equity_curve"],
            benchmark["initial_cash"],
        )       

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"多股票回測失敗：{error}")
        return 1

    first_date = result["equity_curve"][0]["date"]
    last_date = result["equity_curve"][-1]["date"]

    print("多股票回測：每日排名，下一行情日開盤調整持股")
    print("含設定費稅；未計滑價、股利、拆股與精確零股成交")
    print("期末持股按收盤價估值，不強制賣出")
    print(f"\n股票池：{', '.join(stocks)}")
    print(f"資料庫位置：{db_path}")
    print(f"指定期間：{start} ～ {end}")
    print(f"實際期間：{first_date} ～ {last_date}")
    print(f"回測行情日數：{len(result['equity_curve'])}")

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
        print(
            f"排名依據："
            f"{screening_settings['momentum_window']} 期價格報酬率"
        )
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
    print(f"每次買進股數：{backtest_settings['quantity']}")

    print(f"\n手續費率：{cost_settings.commission_rate:.4%}")
    print(f"折扣倍率：{cost_settings.commission_discount}")
    print(f"最低手續費：{cost_settings.minimum_commission} 元")
    print(f"賣出交易稅率：{cost_settings.sell_tax_rate:.2%}")
    print(f"手續費取整：{cost_settings.commission_rounding}")
    print(f"交易稅取整：{cost_settings.tax_rounding}")

    print(f"\n初始資金：{result['initial_cash']:.2f}")
    print(f"期末現金：{result['cash']:.2f}")
    print(f"期末總資產：{result['final_equity']:.2f}")
    print(f"區間報酬率：{result['total_return']:.4%}")
    print(f"累計手續費：{result['total_commission']:.2f}")
    print(f"累計交易稅：{result['total_tax']:.2f}")
    print(f"成交筆數：{len(result['trades'])}")
    print("\n績效統計：")
    print(
        f"最大回撤（每日收盤）："
        f"{performance['max_drawdown']:.4%}"
    )
    print(
        f"最高收盤總資產：{performance['highest_equity']:.2f}"
        f"（{performance['highest_date']}）"
    )
    print(
        f"最低收盤總資產：{performance['lowest_equity']:.2f}"
        f"（{performance['lowest_date']}）"
    )
    print(f"持股天數：{performance['invested_days']}")
    print(f"空手天數：{performance['cash_only_days']}")
    print(
        f"持股天數占比："
        f"{performance['invested_day_ratio']:.2%}"
    )
    print("\n買進持有基準比較：")
    print("基準：每檔在期間內第一個可交易日，以等預算買進")
    print("基準股數依預算計算，不使用策略的固定股數或持股上限")
    print(
        f"策略期末總資產：{result['final_equity']:.2f}"
        f" | 基準：{benchmark['final_equity']:.2f}"
    )
    print(
        f"策略報酬率：{performance['total_return']:.4%}"
        f" | 基準：{benchmark_performance['total_return']:.4%}"
    )
    print(
        f"策略最大回撤：{performance['max_drawdown']:.4%}"
        f" | 基準：{benchmark_performance['max_drawdown']:.4%}"
    )

    return_difference = (
        performance["total_return"]
        - benchmark_performance["total_return"]
    )
    print(
        f"報酬率差（策略－基準）："
        f"{return_difference * 100:+.4f} 個百分點"
    )

    print(
        f"策略累計費稅："
        f"{result['total_commission'] + result['total_tax']:.2f}"
        f" | 基準："
        f"{benchmark['total_commission'] + benchmark['total_tax']:.2f}"
    )

    print("基準持股：")
    for stock_id, shares in sorted(benchmark["holdings"].items()):
        print(f"  {stock_id}：{shares} 股")

    if benchmark["unbought"]:
        print(
            "基準因單檔預算不足而未買進："
            + ", ".join(benchmark["unbought"])
        )
    if benchmark["unavailable_entire_period"]:
        print(
            "基準因整段期間皆無法交易而未買進："
            + ", ".join(benchmark["unavailable_entire_period"])
        )
    print("\n期末持股：")

    if not result["holdings"]:
        print("無")
    else:
        for stock_id, quantity in sorted(result["holdings"].items()):
            print(f"{stock_id}：{quantity} 股")

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
        f"\n有個別股票未更新訊號的日期："
        f"{len(result['skipped_rebalances'])} 日"
    )

    for item in result["skipped_rebalances"]:
        reasons = "；".join(
            f"{stock_id}：{message}"
            for stock_id, message in item["errors"].items()
        )
        print(f"{item['date']} | {reasons}")

    if args.show_equity:
        print("\n每日資產：")
        print("日期 | 現金 | 持股市值 | 總資產 | 持股")

        for point in result["equity_curve"]:
            holdings_text = ", ".join(
                f"{stock_id}:{quantity}"
                for stock_id, quantity in sorted(
                    point["holdings"].items()
                )
            ) or "無"

            print(
                f"{point['date']}"
                f" | {point['cash']:.2f}"
                f" | {point['market_value']:.2f}"
                f" | {point['equity']:.2f}"
                f" | {holdings_text}"
            )
    if args.export:
        output_root = (
            Path(args.output_dir)
            if args.output_dir is not None
            else DEFAULT_CONFIG_PATH.parent / "reports"
        )

        try:
            report_dir = export_portfolio_report(
                output_root,
                settings=settings,
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())