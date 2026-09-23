from datetime import date

from tw_stock_backtest.analysis.screening import evaluate_stock
from tw_stock_backtest.date_range import parse_date_range


def build_screening_history(
    records_by_stock,
    start_text,
    end_text,
    *,
    screening_settings,
):
    """逐日產生候選名單；不模擬交易。"""
    start, end = parse_date_range(start_text, end_text)

    top_n = screening_settings["top_n"]

    if (
        isinstance(top_n, bool)
        or not isinstance(top_n, int)
        or top_n <= 0
    ):
        raise ValueError("top_n 必須是正整數")

    parameters = {
        key: screening_settings[key]
        for key in (
            "short_window",
            "long_window",
            "volume_window",
            "min_volume_ratio",
            "momentum_window",
        )
    }

    # 先檢查每檔股票的日期順序與股票代號。
    # 日期集合取自資料中的實際日期，不自行假定平日都有交易。
    analysis_dates = set()

    for stock_id, records in records_by_stock.items():
        previous_date = None

        for record in records:
            current_date = date.fromisoformat(record["date"])

            if record["stock_id"] != stock_id:
                raise ValueError(f"{stock_id} 的行情混入其他股票")

            if (
                previous_date is not None
                and current_date <= previous_date
            ):
                raise ValueError(
                    f"{stock_id} 的行情日期必須遞增且不可重複"
                )

            previous_date = current_date

            if start <= current_date <= end:
                analysis_dates.add(current_date)

    # 隨日期向前推進，逐步加入當時已知的行情。
    available = {
        stock_id: []
        for stock_id in records_by_stock
    }
    positions = {
        stock_id: 0
        for stock_id in records_by_stock
    }

    history = []

    for analysis_date in sorted(analysis_dates):
        as_of = analysis_date.isoformat()
        evaluations = []
        errors = {}

        for stock_id, records in records_by_stock.items():
            position = positions[stock_id]

            while (
                position < len(records)
                and date.fromisoformat(
                    records[position]["date"]
                ) <= analysis_date
            ):
                available[stock_id].append(records[position])
                position += 1

            positions[stock_id] = position

            try:
                result = evaluate_stock(
                    available[stock_id],
                    as_of,
                    **parameters,
                )
                evaluations.append(result)

            except ValueError as error:
                errors[stock_id] = str(error)

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

        history.append(
            {
                "date": as_of,
                "evaluated_count": len(evaluations),
                "matched_count": len(candidates),
                "candidates": candidates[:top_n],
                "errors": errors,
            }
        )

    return history