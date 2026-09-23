import argparse
import sqlite3
from datetime import date

from database import load_records
from screening import evaluate_stock


def main():
    parser = argparse.ArgumentParser(
        description="依價格趨勢與成交量篩選候選股票"
    )

    parser.add_argument(
        "--stocks",
        nargs="+",
        required=True,
        help="股票代號清單，以空白分隔",
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="分析日期，必須有當日行情",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="最多列出幾檔通過條件的股票",
    )

    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.as_of).isoformat()

        if args.top <= 0:
            raise ValueError("--top 必須大於 0")

    except ValueError as error:
        parser.error(str(error))

    # 去除重複代號，保留原本順序。
    stock_ids = list(dict.fromkeys(args.stocks))

    evaluations = []
    errors = []

    for stock_id in stock_ids:
        try:
            # 先讀取資料庫中截至分析日的歷史行情。
            records = load_records(
                stock_id,
                "1900-01-01",
                as_of,
            )

            result = evaluate_stock(records, as_of)
            evaluations.append(result)

        except (ValueError, sqlite3.Error, OSError) as error:
            errors.append((stock_id, str(error)))

    print(f"分析日期：{as_of}")
    print(f"指定股票數：{len(stock_ids)}")
    print(f"成功評估：{len(evaluations)}")
    print(f"無法評估：{len(errors)}")

    print("\n各股票結果：")

    for item in evaluations:
        status = "通過" if item["selected"] else "未通過"

        print(
            f"{item['stock_id']} | {status} | "
            f"收盤 {item['close']:.2f} | "
            f"SMA5 {item['sma5']:.2f} | "
            f"SMA20 {item['sma20']:.2f} | "
            f"量比 {item['volume_ratio']:.2f} | "
            f"20期價格報酬 {item['momentum20']:.2%}"
        )

        if item["failed_conditions"]:
            print("  原因：" + "；".join(item["failed_conditions"]))

    for stock_id, message in errors:
        print(f"{stock_id} | 無法評估 | {message}")

    candidates = [
        item for item in evaluations
        if item["selected"]
    ]

    # 報酬率由高到低；相同時依代號排列。
    candidates.sort(
        key=lambda item: (-item["momentum20"], item["stock_id"])
    )

    print("\n候選股排名：")

    if not candidates:
        print("沒有股票通過全部條件。")

    for rank, item in enumerate(candidates[:args.top], start=1):
        print(
            f"{rank}. {item['stock_id']} | "
            f"20期價格報酬 {item['momentum20']:.2%} | "
            f"量比 {item['volume_ratio']:.2f}"
        )

    print("\n排名只代表本版規則的篩選結果，不代表獲利機率。")

    # 任一股票無法評估，讓自動化流程能辨識不完整結果。
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())