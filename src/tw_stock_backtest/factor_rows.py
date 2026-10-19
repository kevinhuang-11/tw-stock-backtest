"""研究排名與因子篩選共用：逐檔讀取行情、檢查分析日、計算因子並收集無法評估的原因。"""

from tw_stock_backtest.analysis.factors import calculate_factor_row
from tw_stock_backtest.data.database import load_records, market_has_rows
from tw_stock_backtest.data.market_events import CONFIRMED_DELISTINGS, CONFIRMED_HALTS


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
