import argparse
import sqlite3
from collections import Counter
from types import SimpleNamespace

from tw_stock_backtest.analysis.adjustment import AdjustedHistory
from tw_stock_backtest.analysis.factors import (
    calculate_factor_row,
    rank_factor_candidates,
)
from tw_stock_backtest.analysis.forward_returns import evaluate_rankings
from tw_stock_backtest.analysis.screening import evaluate_stock
from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.data.market_events import (
    CONFIRMED_HALTS,
    not_tradable_reason,
    prepare_backtest_market,
    unconfirmed_gaps,
)
from tw_stock_backtest.data.universe import listing_exclusions
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.prices import PriceSource, describe_prices, forward_return_scope


def parse_arguments():
    parser = argparse.ArgumentParser(description="評估候選股排名的後續價格報酬")
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


REASON_LABELS = {
    "ranking_incomplete": "排名不完整",
    "no_candidates": "沒有候選股",
    "incomplete_forward_returns": "後續報酬資料不完整",
}
DETAIL_LABELS = {
    "insufficient_future_dates": "未來行情日數不足",
    "missing_entry_record": "缺少起算日行情",
    "missing_exit_record": "缺少結束日行情",
    "unavailable_entry_price": "起算日價格無效或不可交易",
    "unavailable_exit_price": "結束日價格無效或不可交易",
}


def _load(args):
    """讀設定、排除股票、載入行情並建立暖機序列；失敗時丟出例外，由 main 處理。"""
    start, end = parse_date_range(args.start, args.end)
    settings = load_config(args.config)

    for name, values in (
        ("horizons", args.horizons),
        ("top", args.top),
    ):
        if any(value <= 0 for value in values) or len(set(values)) != len(values):
            raise ValueError(f"{name} 必須是正整數且不可重複")

    stocks = settings["universe"]["stocks"]
    db_path = settings["storage"]["database_path"]

    # 開始日還沒上市或已終止上市的股票不納入股票池（docs/HALT-POLICY.md 3.9、3.10）。
    excluded = listing_exclusions(stocks, db_path, start)
    excluded_ids = {item["stock_id"] for item in excluded}
    stocks = [stock_id for stock_id in stocks if stock_id not in excluded_ids]
    if not stocks:
        raise ValueError("股票池在開始日都還沒上市或已終止上市")

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

    # 保留停牌與無成交價的列，供後續評估判斷起訖日是否可交易。
    records_by_stock = {stock_id: list(prices.values()) for stock_id, prices in market.items()}

    # 排名指標的暖機資料保留不可交易的列，與研究排名使用同一套規則。
    # 訊號依價格模式逐日累積（每個訊號日只套用該日以前生效的公司行動）；
    # 前瞻報酬計入期間內的公司行動（raw 模式只計價格報酬）。
    prices = PriceSource(settings)
    corporate_actions = prices.actions_by_stock(raw_records, end.isoformat())
    histories = {
        stock_id: AdjustedHistory(corporate_actions.get(stock_id, ())).extend(
            record for record in records if record["date"] < start.isoformat()
        )
        for stock_id, records in records_by_stock.items()
    }

    return SimpleNamespace(
        start=start,
        end=end,
        settings=settings,
        stocks=stocks,
        excluded=excluded,
        market=market,
        trading_dates=trading_dates,
        records_by_stock=records_by_stock,
        prices=prices,
        corporate_actions=corporate_actions,
        histories=histories,
    )


def _rank_each_day(args, run):
    """逐個訊號日只用當日以前的資料排名；任何一檔無法評估時，當天不產生候選。"""
    settings, stocks, market, trading_dates, histories = (
        run.settings,
        run.stocks,
        run.market,
        run.trading_dates,
        run.histories,
    )
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

            # 此時只加入訊號日當天及以前的資料。
            histories[stock_id].append(record)

            if not record["tradable"]:
                errors[stock_id] = not_tradable_reason(record)
                continue

            try:
                if args.ranking == "rules":
                    evaluation = evaluate_stock(
                        histories[stock_id].rows,
                        signal_date,
                        **rule_settings,
                    )
                else:
                    evaluation = calculate_factor_row(
                        histories[stock_id].rows,
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
            candidates = [row for row in evaluations if row["selected"]]
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

        ranking_history.append(
            {
                "date": signal_date,
                "candidates": candidates,
                "errors": errors,
            }
        )

    return ranking_history


def _evaluate(args, run, ranking_history):
    """排名全部建立後，才使用後續價格進行事後評分。"""
    settings, trading_dates, records_by_stock, corporate_actions = (
        run.settings,
        run.trading_dates,
        run.records_by_stock,
        run.corporate_actions,
    )
    report = evaluate_rankings(
        ranking_history,
        records_by_stock,
        trading_dates,
        horizons=tuple(args.horizons),
        top_ns=tuple(args.top),
        corporate_actions=corporate_actions,
        delisted_valuation=settings["halts"]["delisted_valuation"],
    )

    return report


def _print_header(args, run, ranking_history):
    """評估方法、股票池、期間、價格口徑與原因未確認的缺列；回傳缺列清單。"""
    start, end, stocks, excluded, market, trading_dates, prices = (
        run.start,
        run.end,
        run.stocks,
        run.excluded,
        run.market,
        run.trading_dates,
        run.prices,
    )
    print("選股能力評估：下一行情日開盤 → 第 N 個行情日收盤")
    print(f"進場日算第 1 日；未計費稅與滑價；{forward_return_scope(prices.mode, prices.rights)}")
    print("每日樣本可能重疊；平均報酬不是累積帳戶報酬")
    print(f"\n股票池：{', '.join(stocks)}")
    for item in excluded:
        print(f"未納入股票池：{item['stock_id']}（{item['reason']}）")
    print(f"排名模式：{args.ranking}")
    print(f"指定訊號期間：{start} ～ {end}")
    print(f"實際訊號期間：{trading_dates[0]} ～ {trading_dates[-1]}")
    print(f"行情資料截止：{end}")
    print(f"訊號日數：{len(ranking_history)}")
    print(describe_prices(prices.describe()))
    print("評估名次使用 --top，不使用設定檔的 top_n")
    gaps = unconfirmed_gaps(market)
    for gap in gaps:
        print(
            f"原因未確認的缺列：{gap['stock_id']} | {gap['first']} ～ {gap['last']}"
            f" | {gap['days']} 個行情日（視為不可交易；結果不完整，退出碼 2）"
        )

    return gaps


def _print_summary(summary, report):
    """一組（觀察日數 × 前 N 名）的評估結果。"""
    horizon = summary["horizon"]
    top_n = summary["top_n"]

    print(f"\n觀察 {horizon} 日／前 {top_n} 名")
    print(f"有效訊號日：{summary['evaluated_days']}／{summary['signal_days']}")
    print(f"候選股平均價格報酬：{format_percent(summary['average_selected_return'])}")
    print(f"股票池平均價格報酬：{format_percent(summary['average_universe_return'])}")

    excess = summary["average_excess_return"]
    excess_text = "--" if excess is None else f"{excess * 100:+.2f} 個百分點"
    print(f"平均報酬差：{excess_text}")
    print(f"候選組合報酬為正的比例：{format_percent(summary['positive_return_ratio'])}")
    print(f"候選組合勝過股票池的比例：{format_percent(summary['outperform_ratio'])}")

    valid_rows = [
        row
        for row in report["details"]
        if row["horizon"] == horizon and row["top_n"] == top_n and row["status"] == "ok"
    ]
    counts = Counter(row["selected_count"] for row in valid_rows)
    counts_text = (
        "、".join(f"{count} 檔：{days} 日" for count, days in sorted(counts.items())) or "無"
    )
    print(f"有效樣本實際候選數：{counts_text}")
    if summary["stale_exits"]:
        print(
            f"出場日不可交易、以最近收盤估值：{summary['stale_exits']} 筆股票案例"
            "（docs/HALT-POLICY.md 3.22）"
        )

    for reason, count in summary["unavailable_reasons"].items():
        label = REASON_LABELS.get(reason, reason)
        print(f"未評估－{label}：{count} 日")

    # 此處計數單位是「股票 × 訊號日」，不是日數。
    unavailable_counts = Counter(
        reason
        for row in report["details"]
        if row["horizon"] == horizon and row["top_n"] == top_n
        for reason in row["unavailable_stocks"].values()
    )
    for reason, count in sorted(unavailable_counts.items()):
        label = DETAIL_LABELS.get(reason, reason)
        print(f"  {label}：{count} 筆股票案例")


def main():
    args = parse_arguments()

    try:
        run = _load(args)
        ranking_history = _rank_each_day(args, run)
        report = _evaluate(args, run, ranking_history)
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"選股評估失敗：{error}")
        return 1

    gaps = _print_header(args, run, ranking_history)
    for summary in report["summaries"]:
        _print_summary(summary, report)

    return 2 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
