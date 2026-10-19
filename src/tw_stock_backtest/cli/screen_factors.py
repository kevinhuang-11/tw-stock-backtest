import argparse
import sqlite3
from datetime import date

from tw_stock_backtest.analysis.factors import (
    calculate_factor_row,
    rank_factor_candidates,
)
from tw_stock_backtest.config import (
    DEFAULT_CONFIG_PATH,
    apply_overrides,
    load_config,
)
from tw_stock_backtest.data.database import load_records, market_has_rows
from tw_stock_backtest.data.market_events import CONFIRMED_DELISTINGS, CONFIRMED_HALTS
from tw_stock_backtest.data.universe import load_universe
from tw_stock_backtest.prices import PriceSource, describe_prices


def parse_arguments():
    parser = argparse.ArgumentParser(description="計算動能、趨勢與低波動因子，產生綜合排名")

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
    prices=None,
):
    """逐檔計算因子，收集成功結果與無法評估的原因。

    prices：PriceSource；提供時依它的模式讀取（adjusted 為截至分析日的還原價），
    省略時讀原始價格。
    """
    factor_rows = []
    errors = []

    for stock in stocks:
        stock_id = stock["stock_id"]

        # 資料庫故障應讓整個命令失敗，
        # 不把系統故障當成個別股票資料不足。
        records = (
            prices.load(stock_id, "1900-01-01", as_of)
            if prices is not None
            else load_records(stock_id, "1900-01-01", as_of, db_path=db_path)
        )

        try:
            if not records:
                raise ValueError("沒有指定日期以前的行情")

            latest_date = records[-1]["date"]

            if latest_date != as_of:
                # 只看分析日當天（docs/HALT-POLICY.md 3.7、3.12），不查之後的行情。
                delisted = CONFIRMED_DELISTINGS.get(stock_id)
                if delisted and delisted["effective"] <= as_of:
                    raise ValueError(
                        f"已於 {delisted['effective']} 終止上市（{delisted['reason']}）；"
                        f"最後行情日期為 {latest_date}"
                    )
                if market_has_rows(as_of, db_path=db_path):
                    raise ValueError(
                        f"{as_of} 其他股票有行情、這檔沒有列（原因未確認，不認定為停牌；"
                        f"可能是行情未更新或下載缺漏）；最後行情日期為 {latest_date}"
                    )
                raise ValueError(
                    f"行情庫在 {as_of} 沒有任何股票的行情（休市日，或行情尚未更新到這天）；"
                    f"這檔最後行情日期為 {latest_date}"
                )

            last = records[-1]
            # 與 prepare_market_records 相同：四個價格欄位都在、而且都是空的。
            if all(
                field in last and last[field] is None for field in ("open", "high", "low", "close")
            ):
                halt = CONFIRMED_HALTS.get((stock_id, as_of))
                if halt:
                    raise ValueError(f"分析日已確認停牌：{halt['reason']}")
                raise ValueError("分析日無成交價（原因未確認，不認定為停牌）")

            row = calculate_factor_row(
                records,
                as_of,
                factor_settings=factor_settings,
            )
            factor_rows.append(row)

        except ValueError as error:
            note = prices.restart_note(stock_id, as_of) if prices is not None else ""
            errors.append(
                {
                    "stock_id": stock_id,
                    "name": stock.get("name", ""),
                    "reason": str(error) + note,
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
                stocks = stocks[: args.limit]

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

        prices = PriceSource(settings)
        factor_rows, errors = collect_factor_rows(
            stocks,
            as_of,
            db_path=db_path,
            factor_settings=factor_settings,
            prices=prices,
        )

        print(f"分析日期：{as_of}")
        print(describe_prices(prices.describe()))
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
                print(f"{item['stock_id']} {item['name']} | {item['reason']}")

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

    metadata = {stock["stock_id"]: stock for stock in stocks}

    print(
        f"\n期間：動能 {factor_settings['momentum_window']}，"
        f"均線 {factor_settings['short_window']}"
        f"/{factor_settings['long_window']}，"
        f"波動 {factor_settings['volatility_window']}"
    )
    print(
        f"權重：動能 {weights['momentum']}，趨勢 {weights['trend']}，低波動 {weights['volatility']}"
    )
    print(f"實際排名母體：{len(factor_rows)} 檔")

    if errors:
        print("本次為部分股票排名，未包含上方無法評估的股票。")

    print("\n因子排名：")

    for row in ranked[: factor_settings["top_n"]]:
        values = row["factors"]
        scores = row["factor_scores"]
        stock = metadata[row["stock_id"]]

        name = stock.get("name", "")
        industry = stock.get("industry", "") or "未提供產業"

        print(f"{row['rank']}. {row['stock_id']} {name} | {industry} | 總分 {row['score']:.2f}")
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

    print("\n分數代表成功評估股票之間的相對排名，不是獲利機率。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
