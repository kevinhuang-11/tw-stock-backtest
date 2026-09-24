import argparse
import sqlite3
from datetime import date

from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records
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
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--stocks", nargs="+", default=None)
    parser.add_argument("--top", type=int, default=None)

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        as_of = date.fromisoformat(args.as_of).isoformat()

        settings = apply_overrides(
            load_config(args.config),
            {
                "universe": {"stocks": args.stocks},
                "factors": {"top_n": args.top},
            },
        )

        stocks = settings["universe"]["stocks"]
        factor_settings = settings["factors"]
        db_path = settings["storage"]["database_path"]

        weights = {
            "momentum": factor_settings["momentum_weight"],
            "trend": factor_settings["trend_weight"],
            "volatility": factor_settings["volatility_weight"],
        }

        factor_rows = []
        errors = []

        for stock_id in stocks:
            records = load_records(
                stock_id,
                "1900-01-01",
                as_of,
                db_path=db_path,
            )

            try:
                row = calculate_factor_row(
                    records,
                    as_of,
                    factor_settings=factor_settings,
                )
                factor_rows.append(row)

            except ValueError as error:
                errors.append((stock_id, str(error)))

        # 相對排名會受到股票池組成影響。
        # 第一版要求整個指定股票池都能評估，避免悄悄改變母體。
        if errors:
            print("股票池資料不完整，本次不產生因子排名。")

            for stock_id, message in errors:
                print(f"{stock_id}：{message}")

            return 1

        ranked = rank_factor_candidates(
            factor_rows,
            weights=weights,
        )

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"因子分析失敗：{error}")
        return 1

    print(f"分析日期：{as_of}")
    print(f"股票池：{', '.join(stocks)}")
    print(f"資料庫位置：{db_path}")
    print(
        f"期間：動能 {factor_settings['momentum_window']}，"
        f"均線 {factor_settings['short_window']}"
        f"/{factor_settings['long_window']}，"
        f"波動 {factor_settings['volatility_window']}"
    )
    print(
        f"權重：動能 {weights['momentum']}，"
        f"趨勢 {weights['trend']}，"
        f"低波動 {weights['volatility']}"
    )

    print("\n因子排名：")

    for row in ranked[:factor_settings["top_n"]]:
        values = row["factors"]
        scores = row["factor_scores"]

        print(
            f"{row['rank']}. {row['stock_id']}"
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

    print("\n分數代表當日股票池內的相對排名，不是獲利機率。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())