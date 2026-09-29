import argparse
import sqlite3
from datetime import date

from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.data.universe import load_universe
from tw_stock_backtest.analysis.factors import (
    calculate_factor_row,
    rank_factor_candidates,
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="計算動能、趨勢與低波動因子，產生綜合排名"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="分析日期，須為資料庫已有行情的日期",
    )

    # 指定股票與讀取名單，兩種來源不可同時使用。
    source = parser.add_mutually_exclusive_group()

    source.add_argument(
        "--stocks",
        nargs="+",
        default=None,
    )
    source.add_argument(
        "--universe",
        default=None,
        help="上市股票名單 JSON 路徑",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只取名單前幾檔；僅搭配 --universe",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="顯示前幾名",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="允許排除無法評估的股票，對其餘股票排名",
    )

    return parser.parse_args()


def collect_factor_rows(
    stocks,
    as_of,
    *,
    db_path,
    factor_settings,
):
    """逐檔計算因子，收集成功結果與無法評估的原因。"""
    factor_rows = []
    errors = []

    for stock in stocks:
        stock_id = stock["stock_id"]

        # 資料庫故障應讓整個命令失敗，
        # 不把系統故障當成個別股票資料不足。
        records = load_records(
            stock_id,
            "1900-01-01",
            as_of,
            db_path=db_path,
        )

        try:
            if not records:
                raise ValueError("沒有指定日期以前的行情")

            latest_date = records[-1]["date"]

            if latest_date != as_of:
                raise ValueError(
                    f"行情未更新至分析日期；"
                    f"最後行情日期為 {latest_date}"
                )

            row = calculate_factor_row(
                records,
                as_of,
                factor_settings=factor_settings,
            )
            factor_rows.append(row)

        except ValueError as error:
            errors.append(
                {
                    "stock_id": stock_id,
                    "name": stock.get("name", ""),
                    "reason": str(error),
                }
            )

    return factor_rows, errors


def main():
    args = parse_arguments()

    try:
        as_of = date.fromisoformat(args.as_of).isoformat()

        if args.limit is not None:
            if args.universe is None:
                raise ValueError("--limit 必須搭配 --universe")
            if args.limit <= 0:
                raise ValueError("--limit 必須大於 0")

        settings = apply_overrides(
            load_config(args.config),
            {
                "universe": {"stocks": args.stocks},
                "factors": {"top_n": args.top},
            },
        )

        snapshot = None

        if args.universe is not None:
            snapshot = load_universe(args.universe)

            stocks = sorted(
                snapshot["stocks"],
                key=lambda stock: stock["stock_id"],
            )

            if args.limit is not None:
                stocks = stocks[:args.limit]

        else:
            stocks = [
                {
                    "stock_id": stock_id,
                    "name": "",
                    "industry": "",
                }
                for stock_id in settings["universe"]["stocks"]
            ]

        factor_settings = settings["factors"]
        db_path = settings["storage"]["database_path"]

        weights = {
            "momentum": factor_settings["momentum_weight"],
            "trend": factor_settings["trend_weight"],
            "volatility": factor_settings["volatility_weight"],
        }

        factor_rows, errors = collect_factor_rows(
            stocks,
            as_of,
            db_path=db_path,
            factor_settings=factor_settings,
        )

        print(f"分析日期：{as_of}")
        print(f"資料庫位置：{db_path}")
        print(f"指定股票數：{len(stocks)}")
        print(f"成功評估：{len(factor_rows)}")
        print(f"無法評估：{len(errors)}")

        if snapshot is not None:
            print(f"名單下載時間：{snapshot['downloaded_at']}")
            print("使用目前上市名單，不代表分析日期的歷史完整股票池。")

        if errors:
            print("\n無法評估的股票：")

            for item in errors:
                print(
                    f"{item['stock_id']} {item['name']}"
                    f" | {item['reason']}"
                )

            if not args.allow_partial:
                print(
                    "\n股票池資料不完整，本次不產生排名。"
                    "\n若接受只對成功評估的股票排名，"
                    "請加上 --allow-partial。"
                )
                return 1

        if not factor_rows:
            print("\n沒有可供排名的股票。")
            return 1

        ranked = rank_factor_candidates(
            factor_rows,
            weights=weights,
        )

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"因子分析失敗：{error}")
        return 1

    metadata = {
        stock["stock_id"]: stock
        for stock in stocks
    }

    print(
        f"\n期間：動能 {factor_settings['momentum_window']}，"
        f"均線 {factor_settings['short_window']}"
        f"/{factor_settings['long_window']}，"
        f"波動 {factor_settings['volatility_window']}"
    )
    print(
        f"權重：動能 {weights['momentum']}，"
        f"趨勢 {weights['trend']}，"
        f"低波動 {weights['volatility']}"
    )
    print(f"實際排名母體：{len(factor_rows)} 檔")

    if errors:
        print("本次為部分股票排名，未包含上方無法評估的股票。")

    print("\n因子排名：")

    for row in ranked[:factor_settings["top_n"]]:
        values = row["factors"]
        scores = row["factor_scores"]
        stock = metadata[row["stock_id"]]

        name = stock.get("name", "")
        industry = stock.get("industry", "") or "未提供產業"

        print(
            f"{row['rank']}. {row['stock_id']} {name}"
            f" | {industry}"
            f" | 總分 {row['score']:.2f}"
        )
        print(
            f"   原始值：動能 {values['momentum']:.2%}"
            f" | 趨勢 {values['trend']:.2%}"
            f" | 日報酬波動 {values['volatility']:.2%}"
        )
        print(
            f"   因子分數：動能 {scores['momentum']:.2f}"
            f" | 趨勢 {scores['trend']:.2f}"
            f" | 低波動 {scores['volatility']:.2f}"
        )

    print(
        "\n分數代表成功評估股票之間的相對排名，"
        "不是獲利機率。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())