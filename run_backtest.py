import argparse
import sqlite3
from decimal import Decimal, InvalidOperation

from backtest import run_backtest
from database import load_records
from indicators import simple_moving_average
from strategies import moving_average_crossover


def main():
    parser = argparse.ArgumentParser(
        description="簡化均線回測：不計交易成本與公司行動"
    )

    parser.add_argument("--stock", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--short", type=int, default=5)
    parser.add_argument("--long", type=int, default=10)
    parser.add_argument("--cash", default="1000000")
    parser.add_argument("--quantity", type=int, default=100)

    args = parser.parse_args()

    try:
        if not (0 < args.short < args.long):
            raise ValueError("均線期間必須符合：0 < 短期 < 長期")

        initial_cash = Decimal(args.cash)

        records = load_records(args.stock, args.start, args.end)

        if not records:
            raise ValueError("資料庫中沒有指定區間的行情")

        closes = [record["close"] for record in records]

        short_ma = simple_moving_average(closes, args.short)
        long_ma = simple_moving_average(closes, args.long)
        signals = moving_average_crossover(short_ma, long_ma)

        result = run_backtest(
            records,
            signals,
            initial_cash,
            args.quantity,
        )

    except (
        ValueError,
        InvalidOperation,
        sqlite3.Error,
        OSError,
    ) as error:
        print(f"回測失敗：{error}")
        return 1

    print("簡化回測：未計交易成本、股利與拆股")
    print("股票代號：", args.stock)
    print("實際期間：", records[0]["date"], "～", records[-1]["date"])
    print(f"初始資金：{result['initial_cash']:.2f}")
    print(f"期末現金：{result['cash']:.2f}")
    print("期末持股：", result["shares"])
    print(f"期末總資產：{result['final_equity']:.2f}")
    print(f"區間報酬率：{result['total_return']:.2%}")
    print("成交筆數：", len(result["trades"]))

    print("\n成交紀錄：")

    if not result["trades"]:
        print("本區間沒有成交。")

    for trade in result["trades"]:
        print(
            f"訊號日 {trade['signal_date']} → "
            f"成交日 {trade['date']}，"
            f"{trade['action']}，"
            f"價格 {trade['price']:.2f}，"
            f"股數 {trade['quantity']}，"
            f"剩餘現金 {trade['cash_after']:.2f}"
        )

    if len(records) <= args.long:
        print("\n提醒：資料不足以比較完整的相鄰兩日長均線。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())