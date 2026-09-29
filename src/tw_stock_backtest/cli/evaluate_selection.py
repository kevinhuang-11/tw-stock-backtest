import argparse
import sqlite3
from collections import Counter

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.data.market_events import (
    CONFIRMED_HALTS,
    prepare_backtest_market,
)
from tw_stock_backtest.analysis.screening import evaluate_stock
from tw_stock_backtest.analysis.factors import (
    calculate_factor_row,
    rank_factor_candidates,
)
from tw_stock_backtest.analysis.forward_returns import evaluate_rankings


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="評估候選股排名的後續價格報酬"
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置",
    )
    parser.add_argument("--start", required=True, help="訊號開始日期")
    parser.add_argument(
        "--end",
        required=True,
        help="訊號與行情資料的截止日期",
    )
    parser.add_argument(
        "--ranking",
        choices=("rules", "factors"),
        default="factors",
        help="排名模式",
    )
    parser.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=[5, 20, 60],
        help="觀察行情日數，預設 5 20 60",
    )
    parser.add_argument(
        "--top",
        type=int,
        nargs="+",
        default=[1, 2],
        help="評估前幾名，預設 1 2",
    )
    return parser.parse_args()


def format_percent(value):
    if value is None:
        return "--"
    return f"{value:.2%}"


def main():
    args = parse_arguments()

    try:
        start, end = parse_date_range(args.start, args.end)
        settings = load_config(args.config)

        for name, values in (
            ("horizons", args.horizons),
            ("top", args.top),
        ):
            if (
                any(value <= 0 for value in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError(f"{name} 必須是正整數且不可重複")

        stocks = settings["universe"]["stocks"]
        db_path = settings["storage"]["database_path"]

        if max(args.top) > len(stocks):
            raise ValueError("評估名次不可超過股票池檔數")

        # 僅讀到 end，不讀取保留期間的未來行情。
        raw_records = {
            stock_id: load_records(
                stock_id,
                "1900-01-01",
                end.isoformat(),
                db_path=db_path,
            )
            for stock_id in stocks
        }

        market, trading_dates = prepare_backtest_market(
            raw_records,
            start,
            end,
            confirmed_halts=CONFIRMED_HALTS,
        )

        # 保留停牌列，供後續評估判斷起訖日是否可交易。
        records_by_stock = {
            stock_id: list(prices.values())
            for stock_id, prices in market.items()
        }

        # 排名指標的暖機資料，只使用開始日前可交易的紀錄。
        histories = {
            stock_id: [
                record
                for record in records
                if record["date"] < start.isoformat()
                and record["tradable"]
            ]
            for stock_id, records in records_by_stock.items()
        }

        rule_settings = {
            key: settings["screening"][key]
            for key in (
                "short_window",
                "long_window",
                "volume_window",
                "min_volume_ratio",
                "momentum_window",
            )
        }
        factor_settings = settings["factors"]
        weights = {
            "momentum": factor_settings["momentum_weight"],
            "trend": factor_settings["trend_weight"],
            "volatility": factor_settings["volatility_weight"],
        }

        ranking_history = []

        for signal_date in trading_dates:
            evaluations = []
            errors = {}

            for stock_id in stocks:
                record = market[stock_id][signal_date]

                if not record["tradable"]:
                    errors[stock_id] = "已確認停牌"
                    continue

                # 此時只加入訊號日當天及以前的資料。
                histories[stock_id].append(record)

                try:
                    if args.ranking == "rules":
                        evaluation = evaluate_stock(
                            histories[stock_id],
                            signal_date,
                            **rule_settings,
                        )
                    else:
                        evaluation = calculate_factor_row(
                            histories[stock_id],
                            signal_date,
                            factor_settings=factor_settings,
                        )

                    evaluations.append(evaluation)

                except ValueError as error:
                    errors[stock_id] = str(error)

            # 選股能力比較要求完整股票池排名。
            # 與「停牌時其他股票仍可交易」的回測規則不同。
            if errors:
                candidates = []
            elif args.ranking == "rules":
                candidates = [
                    row for row in evaluations if row["selected"]
                ]
                candidates.sort(
                    key=lambda row: (
                        -row["momentum"],
                        row["stock_id"],
                    )
                )
            else:
                candidates = rank_factor_candidates(
                    evaluations,
                    weights=weights,
                )

            ranking_history.append({
                "date": signal_date,
                "candidates": candidates,
                "errors": errors,
            })

        # 排名全部建立後，才使用後續價格進行事後評分。
        report = evaluate_rankings(
            ranking_history,
            records_by_stock,
            trading_dates,
            horizons=tuple(args.horizons),
            top_ns=tuple(args.top),
        )

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"選股評估失敗：{error}")
        return 1

    print("選股能力評估：下一行情日開盤 → 第 N 個行情日收盤")
    print("進場日算第 1 日；未計費稅、滑價、股利與拆股")
    print("每日樣本可能重疊；平均報酬不是累積帳戶報酬")
    print(f"\n股票池：{', '.join(stocks)}")
    print(f"排名模式：{args.ranking}")
    print(f"指定訊號期間：{start} ～ {end}")
    print(f"實際訊號期間：{trading_dates[0]} ～ {trading_dates[-1]}")
    print(f"行情資料截止：{end}")
    print(f"訊號日數：{len(ranking_history)}")
    print("評估名次使用 --top，不使用設定檔的 top_n")

    reason_labels = {
        "ranking_incomplete": "排名不完整",
        "no_candidates": "沒有候選股",
        "incomplete_forward_returns": "後續報酬資料不完整",
    }
    detail_labels = {
        "insufficient_future_dates": "未來行情日數不足",
        "missing_entry_record": "缺少起算日行情",
        "missing_exit_record": "缺少結束日行情",
        "unavailable_entry_price": "起算日價格無效或不可交易",
        "unavailable_exit_price": "結束日價格無效或不可交易",
    }

    for summary in report["summaries"]:
        horizon = summary["horizon"]
        top_n = summary["top_n"]

        print(f"\n觀察 {horizon} 日／前 {top_n} 名")
        print(
            f"有效訊號日：{summary['evaluated_days']}"
            f"／{summary['signal_days']}"
        )
        print(
            f"候選股平均價格報酬："
            f"{format_percent(summary['average_selected_return'])}"
        )
        print(
            f"股票池平均價格報酬："
            f"{format_percent(summary['average_universe_return'])}"
        )

        excess = summary["average_excess_return"]
        excess_text = (
            "--" if excess is None
            else f"{excess * 100:+.2f} 個百分點"
        )
        print(f"平均報酬差：{excess_text}")
        print(
            f"候選組合報酬為正的比例："
            f"{format_percent(summary['positive_return_ratio'])}"
        )
        print(
            f"候選組合勝過股票池的比例："
            f"{format_percent(summary['outperform_ratio'])}"
        )

        valid_rows = [
            row for row in report["details"]
            if row["horizon"] == horizon
            and row["top_n"] == top_n
            and row["status"] == "ok"
        ]
        counts = Counter(
            row["selected_count"] for row in valid_rows
        )
        counts_text = "、".join(
            f"{count} 檔：{days} 日"
            for count, days in sorted(counts.items())
        ) or "無"
        print(f"有效樣本實際候選數：{counts_text}")

        for reason, count in summary["unavailable_reasons"].items():
            label = reason_labels.get(reason, reason)
            print(f"未評估－{label}：{count} 日")

        # 此處計數單位是「股票 × 訊號日」，不是日數。
        unavailable_counts = Counter(
            reason
            for row in report["details"]
            if row["horizon"] == horizon and row["top_n"] == top_n
            for reason in row["unavailable_stocks"].values()
        )
        for reason, count in sorted(unavailable_counts.items()):
            label = detail_labels.get(reason, reason)
            print(f"  {label}：{count} 筆股票案例")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())