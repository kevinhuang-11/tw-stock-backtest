import argparse
import sqlite3

from tw_stock_backtest.backtesting.backtest import run_backtest
from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.data.database import load_records
from tw_stock_backtest.analysis.indicators import simple_moving_average
from tw_stock_backtest.analysis.strategies import moving_average_crossover


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="執行均線交叉策略回測，包含設定的交易費稅"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置，預設使用專案中的 config.toml",
    )
    parser.add_argument(
        "--stock",
        required=True,
        help="股票代號，例如 2330",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="開始日期，例如 2026-07-01",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="結束日期，例如 2026-09-22",
    )
    parser.add_argument(
        "--short",
        type=int,
        default=None,
        help="短期均線期間；未指定時使用設定檔",
    )
    parser.add_argument(
        "--long",
        type=int,
        default=None,
        help="長期均線期間；未指定時使用設定檔",
    )
    parser.add_argument(
        "--cash",
        default=None,
        help="初始資金；未指定時使用設定檔",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        default=None,
        help="每次買進股數；未指定時使用設定檔",
    )
    parser.add_argument(
        "--fee-rate",
        default=None,
        help="手續費率，例如 0.001425",
    )
    parser.add_argument(
        "--fee-discount",
        default=None,
        help="手續費折扣倍率，例如 0.6",
    )
    parser.add_argument(
        "--min-fee",
        default=None,
        help="最低手續費，例如 20",
    )
    parser.add_argument(
        "--tax-rate",
        default=None,
        help="賣出交易稅率，例如 0.003",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        settings = load_config(args.config)

        settings = apply_overrides(
            settings,
            {
                "strategy": {
                    "short_window": args.short,
                    "long_window": args.long,
                },
                "backtest": {
                    "initial_cash": args.cash,
                    "quantity": args.quantity,
                },
                "costs": {
                    "commission_rate": args.fee_rate,
                    "commission_discount": args.fee_discount,
                    "minimum_commission": args.min_fee,
                    "sell_tax_rate": args.tax_rate,
                },
            },
        )

        stock_id = args.stock.strip()
        if not stock_id:
            raise ValueError("股票代號不可為空白")

        short_window = settings["strategy"]["short_window"]
        long_window = settings["strategy"]["long_window"]
        initial_cash = settings["backtest"]["initial_cash"]
        quantity = settings["backtest"]["quantity"]
        db_path = settings["storage"]["database_path"]

        # config 已經將金額與費率轉成 Decimal。
        cost_settings = CostSettings(**settings["costs"])

        records = load_records(
            stock_id,
            args.start,
            args.end,
            db_path=db_path,
        )

        if not records:
            print("資料庫中沒有符合條件的行情，請先下載資料。")
            return 0

        closes = [record["close"] for record in records]

        short_ma = simple_moving_average(
            closes,
            short_window,
        )
        long_ma = simple_moving_average(
            closes,
            long_window,
        )

        signals = moving_average_crossover(
            short_ma,
            long_ma,
        )

        result = run_backtest(
            records,
            signals,
            initial_cash=initial_cash,
            quantity=quantity,
            cost_settings=cost_settings,
        )

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"回測失敗：{error}")
        return 1

    rounding_names = {
        "ROUND_DOWN": "無條件捨去至整數元",
        "ROUND_HALF_UP": "四捨五入至整數元",
    }

    print("回測：含設定費稅，未計滑價、股利與拆股")
    print("成交價格仍以日線開盤價模擬，未精確模擬零股成交")
    print(f"股票代號：{stock_id}")
    print(f"資料庫位置：{db_path}")
    print(
        f"實際期間：{records[0]['date']}"
        f" ～ {records[-1]['date']}"
    )
    print(
        f"均線期間：短期 {short_window}，"
        f"長期 {long_window}"
    )
    print(f"每次買進股數：{quantity}")

    print(f"\n手續費率：{cost_settings.commission_rate:.4%}")
    print(f"折扣倍率：{cost_settings.commission_discount}")
    print(f"最低手續費：{cost_settings.minimum_commission} 元")
    print(f"賣出交易稅率：{cost_settings.sell_tax_rate:.2%}")
    print(
        "手續費取整："
        + rounding_names[cost_settings.commission_rounding]
    )
    print(
        "交易稅取整："
        + rounding_names[cost_settings.tax_rounding]
    )

    print(f"\n初始資金：{result['initial_cash']:.2f}")
    print(f"期末現金：{result['cash']:.2f}")
    print(f"期末持股：{result['shares']}")
    print(f"期末總資產：{result['final_equity']:.2f}")
    print(f"區間報酬率：{result['total_return']:.4%}")
    print(f"累計手續費：{result['total_commission']:.2f}")
    print(f"累計交易稅：{result['total_tax']:.2f}")
    print(f"成交筆數：{len(result['trades'])}")

    print("\n成交紀錄：")

    if not result["trades"]:
        print("本區間沒有成交。")
    else:
        for trade in result["trades"]:
            print(
                f"訊號日 {trade['signal_date']}"
                f" → 成交日 {trade['date']}，"
                f"{trade['action']}，"
                f"價格 {trade['price']:.2f}，"
                f"股數 {trade['quantity']}，"
                f"手續費 {trade['commission']}，"
                f"交易稅 {trade['tax']}，"
                f"剩餘現金 {trade['cash_after']:.2f}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())