import argparse
import sqlite3
from decimal import Decimal, InvalidOperation

from backtest import run_backtest
from costs import CostSettings
from database import load_records
from indicators import simple_moving_average
from strategies import moving_average_crossover


def main():
    parser = argparse.ArgumentParser(
        description="均線回測：含可設定費稅，未計滑價與公司行動"
    )

    parser.add_argument("--stock", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--short", type=int, default=5)
    parser.add_argument("--long", type=int, default=10)
    parser.add_argument("--cash", default="1000000")
    parser.add_argument("--quantity", type=int, default=100)

    parser.add_argument(
        "--fee-rate",
        default="0.001425",
        help="手續費率，使用小數格式",
    )
    parser.add_argument(
        "--fee-discount",
        default="1",
        help="手續費折扣倍率，例如六折填 0.6",
    )
    parser.add_argument(
        "--min-fee",
        default="20",
        help="每筆最低手續費，單位為元",
    )
    parser.add_argument(
        "--tax-rate",
        default="0.003",
        help="賣出交易稅率，使用小數格式",
    )

    args = parser.parse_args()

    try:
        if not (0 < args.short < args.long):
            raise ValueError("均線期間必須符合：0 < 短期 < 長期")

        initial_cash = Decimal(args.cash)

        settings = CostSettings(
            commission_rate=Decimal(args.fee_rate),
            commission_discount=Decimal(args.fee_discount),
            minimum_commission=Decimal(args.min_fee),
            sell_tax_rate=Decimal(args.tax_rate),
        )

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
            cost_settings=settings,
        )

    except (
        ValueError,
        InvalidOperation,
        sqlite3.Error,
        OSError,
    ) as error:
        print(f"回測失敗：{error}")
        return 1

    print("回測：含設定費稅，未計滑價、股利與拆股")
    print("成交價格仍以日線開盤價模擬，未精確模擬零股成交")
    print("股票代號：", args.stock)
    print("實際期間：", records[0]["date"], "～", records[-1]["date"])

    print(f"\n手續費率：{settings.commission_rate:.4%}")
    print(f"折扣倍率：{settings.commission_discount}")
    print(f"最低手續費：{settings.minimum_commission:.0f} 元")
    print(f"賣出交易稅率：{settings.sell_tax_rate:.2%}")
    print("取整方式：手續費與交易稅皆無條件捨去至整數元")

    print(f"\n初始資金：{result['initial_cash']:.2f}")
    print(f"期末現金：{result['cash']:.2f}")
    print("期末持股：", result["shares"])
    print(f"期末總資產：{result['final_equity']:.2f}")
    print(f"區間報酬率：{result['total_return']:.4%}")
    print(f"累計手續費：{result['total_commission']:.2f}")
    print(f"累計交易稅：{result['total_tax']:.2f}")
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
            f"手續費 {trade['commission']:.0f}，"
            f"交易稅 {trade['tax']:.0f}，"
            f"剩餘現金 {trade['cash_after']:.2f}"
        )

    if len(records) <= args.long:
        print("\n提醒：資料不足以比較完整的相鄰兩日長均線。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())