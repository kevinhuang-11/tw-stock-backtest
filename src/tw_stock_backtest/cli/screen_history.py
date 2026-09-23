import argparse
import sqlite3

from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.date_range import parse_date_range
from tw_stock_backtest.analysis.screening_history import (
    build_screening_history,
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="依歷史日期逐日篩選與排名股票"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
    )
    parser.add_argument(
        "--stocks",
        nargs="+",
        default=None,
        help="未指定時使用 config 的股票池",
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--top", type=int, default=None)

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        start, end = parse_date_range(args.start, args.end)

        settings = apply_overrides(
            load_config(args.config),
            {
                "universe": {"stocks": args.stocks},
                "screening": {"top_n": args.top},
            },
        )

        stocks = settings["universe"]["stocks"]
        db_path = settings["storage"]["database_path"]

        # 包含開始日前的行情，供均線與量比暖機使用。
        records_by_stock = {
            stock_id: load_records(
                stock_id,
                "1900-01-01",
                end.isoformat(),
                db_path=db_path,
            )
            for stock_id in stocks
        }

        history = build_screening_history(
            records_by_stock,
            start.isoformat(),
            end.isoformat(),
            screening_settings=settings["screening"],
        )

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"歷史篩選失敗：{error}")
        return 1

    print(f"股票池：{', '.join(stocks)}")
    print(f"指定期間：{start} ～ {end}")
    print(f"資料庫位置：{db_path}")
    print(f"評估日期數：{len(history)}")
    print("以下為歷史候選名單，尚未模擬交易。\n")

    if not history:
        print("指定期間沒有可評估的行情日期。")
        return 1

    has_errors = False

    for day in history:
        candidates_text = ", ".join(
            result["stock_id"]
            for result in day["candidates"]
        ) or "無"

        print(
            f"{day['date']}"
            f" | 成功評估 {day['evaluated_count']}/{len(stocks)}"
            f" | 通過 {day['matched_count']}"
            f" | 候選排名：{candidates_text}"
        )

        for stock_id, message in day["errors"].items():
            has_errors = True
            print(f"  無法評估 {stock_id}：{message}")

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())