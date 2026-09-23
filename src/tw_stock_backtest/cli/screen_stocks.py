import argparse
import sqlite3
from datetime import date

from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.analysis.screening import evaluate_stock


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="依照趨勢與成交量條件篩選股票並排名"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置，預設使用專案中的 config.toml",
    )
    parser.add_argument(
        "--stocks",
        nargs="+",
        default=None,
        help="股票代號；未指定時使用設定檔的股票池",
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="分析日期，例如 2026-09-22",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="最多顯示幾檔候選股；未指定時使用設定檔",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        as_of = date.fromisoformat(args.as_of).isoformat()

        settings = load_config(args.config)

        settings = apply_overrides(
            settings,
            {
                "universe": {
                    "stocks": args.stocks,
                },
                "screening": {
                    "top_n": args.top,
                },
            },
        )

    except (ValueError, OSError) as error:
        print(f"設定或參數錯誤：{error}")
        return 1

    stock_ids = settings["universe"]["stocks"]
    screening_settings = settings["screening"]
    db_path = settings["storage"]["database_path"]

    short_window = screening_settings["short_window"]
    long_window = screening_settings["long_window"]
    volume_window = screening_settings["volume_window"]
    min_volume_ratio = screening_settings["min_volume_ratio"]
    momentum_window = screening_settings["momentum_window"]
    top_n = screening_settings["top_n"]

    evaluations = []
    errors = []

    for stock_id in stock_ids:
        try:
            records = load_records(
                stock_id,
                "1900-01-01",
                as_of,
                db_path=db_path,
            )

            result = evaluate_stock(
                records,
                as_of,
                short_window=short_window,
                long_window=long_window,
                volume_window=volume_window,
                min_volume_ratio=min_volume_ratio,
                momentum_window=momentum_window,
            )

            evaluations.append(result)

        except (ValueError, sqlite3.Error, OSError) as error:
            errors.append((stock_id, str(error)))

    print(f"分析日期：{as_of}")
    print(f"資料庫位置：{db_path}")
    print(
        f"趨勢條件：Close > SMA{short_window} > SMA{long_window}"
    )
    print(
        f"量比條件：當日量 / 前 {volume_window} 筆均量"
        f" > {min_volume_ratio}"
    )
    print(
        f"排名依據：{momentum_window} 期價格報酬率，"
        f"最多顯示 {top_n} 檔"
    )
    print(f"指定股票數：{len(stock_ids)}")
    print(f"成功評估：{len(evaluations)}")
    print(f"無法評估：{len(errors)}")

    print("\n各股票結果：")

    for result in evaluations:
        status = "通過" if result["selected"] else "未通過"

        print(
            f"{result['stock_id']} | {status}"
            f" | 收盤 {result['close']:.2f}"
            f" | SMA{short_window} {result['short_ma']:.2f}"
            f" | SMA{long_window} {result['long_ma']:.2f}"
            f" | 量比 {result['volume_ratio']:.2f}"
            f" | {momentum_window}期價格報酬 "
            f"{result['momentum']:.2%}"
        )

        if result["failed_conditions"]:
            print(
                "  原因："
                + "；".join(result["failed_conditions"])
            )

    if errors:
        print("\n無法評估的股票：")

        for stock_id, message in errors:
            print(f"{stock_id}：{message}")

    candidates = [
        result
        for result in evaluations
        if result["selected"]
    ]

    candidates.sort(
        key=lambda result: (
            -result["momentum"],
            result["stock_id"],
        )
    )

    print("\n候選股排名：")

    if not candidates:
        print("沒有符合全部條件的候選股。")
    else:
        for rank, result in enumerate(
            candidates[:top_n],
            start=1,
        ):
            print(
                f"{rank}. {result['stock_id']}"
                f" | {momentum_window}期價格報酬 "
                f"{result['momentum']:.2%}"
                f" | 量比 {result['volume_ratio']:.2f}"
            )

    print("\n排名只代表本版規則的篩選結果，不代表獲利機率。")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())