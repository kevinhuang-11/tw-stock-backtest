from decimal import Decimal
from datetime import date
from tw_stock_backtest.date_range import parse_date_range


FACTOR_DIRECTIONS = {
    "momentum": True,
    "trend": True,
    "volatility": False,
}


def _rank_scores(values, *, higher_is_better):
    """將因子值轉成 0～100 分；同值使用平均排名。"""
    count = len(values)

    if count == 0:
        return []

    if count == 1:
        return [Decimal("50")]

    ordered_indices = sorted(
        range(count),
        key=lambda index: values[index],
    )

    scores = [Decimal("0")] * count
    start = 0

    while start < count:
        end = start + 1

        # 找出這一組相同數值。
        while (
            end < count
            and values[ordered_indices[end]]
            == values[ordered_indices[start]]
        ):
            end += 1

        # start 到 end - 1 的平均排名，排名從 0 開始。
        average_rank = (
            Decimal(start) + Decimal(end - 1)
        ) / Decimal("2")

        score = (
            average_rank
            / Decimal(count - 1)
            * Decimal("100")
        )

        if not higher_is_better:
            score = Decimal("100") - score

        for position in range(start, end):
            original_index = ordered_indices[position]
            scores[original_index] = score

        start = end

    return scores


def rank_factor_candidates(factor_rows, *, weights):
    """
    對同一天的股票進行多因子評分。

    factor_rows 每筆包含：
        stock_id、date、momentum、trend、volatility。

    weights 必須提供三個因子的非負 Decimal 權重。
    權重不必加總為 1，函式會除以權重總和。
    """
    if set(weights) != set(FACTOR_DIRECTIONS):
        raise ValueError(
            "權重必須完整提供 momentum、trend、volatility"
        )

    for name, weight in weights.items():
        if (
            not isinstance(weight, Decimal)
            or not weight.is_finite()
            or weight < 0
        ):
            raise ValueError(
                f"{name} 權重必須是非負且有限的 Decimal"
            )

    total_weight = sum(weights.values(), Decimal("0"))

    if total_weight == 0:
        raise ValueError("權重不可全部為 0")

    if not factor_rows:
        return []

    analysis_date = factor_rows[0]["date"]
    seen_stock_ids = set()

    for row in factor_rows:
        stock_id = row["stock_id"]

        if (
            not isinstance(stock_id, str)
            or not stock_id
            or stock_id != stock_id.strip()
        ):
            raise ValueError("股票代號必須是非空且無前後空白的字串")

        if stock_id in seen_stock_ids:
            raise ValueError("同一天不可有重複股票")

        seen_stock_ids.add(stock_id)

        if row["date"] != analysis_date:
            raise ValueError("一次排名只能包含同一天的股票")

        for factor in FACTOR_DIRECTIONS:
            value = row[factor]

            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
            ):
                raise ValueError(
                    f"{stock_id} 的 {factor} 必須是有限的 Decimal"
                )

        if row["volatility"] < 0:
            raise ValueError("波動程度不可為負數")

    scores_by_factor = {}

    for factor, higher_is_better in FACTOR_DIRECTIONS.items():
        scores_by_factor[factor] = _rank_scores(
            [row[factor] for row in factor_rows],
            higher_is_better=higher_is_better,
        )

    ranked = []

    for index, row in enumerate(factor_rows):
        factor_scores = {
            factor: scores_by_factor[factor][index]
            for factor in FACTOR_DIRECTIONS
        }

        weighted_score = sum(
            (
                factor_scores[factor] * weights[factor]
                for factor in FACTOR_DIRECTIONS
            ),
            Decimal("0"),
        ) / total_weight

        ranked.append(
            {
                "stock_id": row["stock_id"],
                "date": row["date"],
                "factors": {
                    factor: row[factor]
                    for factor in FACTOR_DIRECTIONS
                },
                "factor_scores": factor_scores,
                "score": weighted_score,
            }
        )

    ranked.sort(
        key=lambda row: (
            -row["score"],
            row["stock_id"],
        )
    )

    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank

    return ranked

def calculate_factor_row(records, as_of, *, factor_settings):
    """使用分析日期當天及之前的收盤價，計算單檔股票因子。"""
    short_window = factor_settings["short_window"]
    long_window = factor_settings["long_window"]
    momentum_window = factor_settings["momentum_window"]
    volatility_window = factor_settings["volatility_window"]

    for name, value in (
        ("short_window", short_window),
        ("long_window", long_window),
        ("momentum_window", momentum_window),
        ("volatility_window", volatility_window),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise ValueError(f"{name} 必須是正整數")

    if short_window >= long_window:
        raise ValueError("短期均線期間必須小於長期均線期間")

    target_date = date.fromisoformat(as_of)

    if not records:
        raise ValueError("沒有可用行情")

    stock_id = records[0]["stock_id"]
    history = []
    previous_date = None

    for record in records:
        current_date = date.fromisoformat(record["date"])

        if record["stock_id"] != stock_id:
            raise ValueError("行情不可混合不同股票")

        if (
            previous_date is not None
            and current_date <= previous_date
        ):
            raise ValueError("行情日期必須遞增且不可重複")

        previous_date = current_date

        if current_date <= target_date:
            history.append(record)

    if (
        not history
        or history[-1]["date"] != target_date.isoformat()
    ):
        raise ValueError("缺少分析日期當天的行情")

    required_records = max(
        long_window,
        momentum_window + 1,
        volatility_window + 1,
    )

    if len(history) < required_records:
        raise ValueError(
            f"至少需要 {required_records} 筆行情，"
            f"目前只有 {len(history)} 筆"
        )

    closes = [
        record["close"]
        for record in history[-required_records:]
    ]

    for close in closes:
        if (
            not isinstance(close, Decimal)
            or not close.is_finite()
            or close <= 0
        ):
            raise ValueError("收盤價必須是正值且有限的 Decimal")

    short_ma = (
        sum(closes[-short_window:], Decimal("0"))
        / Decimal(short_window)
    )
    long_ma = (
        sum(closes[-long_window:], Decimal("0"))
        / Decimal(long_window)
    )

    momentum = (
        closes[-1] / closes[-(momentum_window + 1)]
        - Decimal("1")
    )
    trend = short_ma / long_ma - Decimal("1")

    # N 個每日報酬率需要 N + 1 筆收盤價。
    volatility_prices = closes[-(volatility_window + 1):]

    daily_returns = [
        current / previous - Decimal("1")
        for previous, current in zip(
            volatility_prices,
            volatility_prices[1:],
        )
    ]

    mean_return = (
        sum(daily_returns, Decimal("0"))
        / Decimal(volatility_window)
    )

    variance = (
        sum(
            (
                (value - mean_return) ** 2
                for value in daily_returns
            ),
            Decimal("0"),
        )
        / Decimal(volatility_window)
    )

    return {
        "stock_id": stock_id,
        "date": target_date.isoformat(),
        "momentum": momentum,
        "trend": trend,
        "volatility": variance.sqrt(),
    }

def build_factor_history(
    records_by_stock,
    start_text,
    end_text,
    *,
    factor_settings,
):
    """逐日計算因子排名，輸出回測引擎使用的候選名單。"""
    start, end = parse_date_range(start_text, end_text)
    top_n = factor_settings["top_n"]

    if (
        isinstance(top_n, bool)
        or not isinstance(top_n, int)
        or top_n <= 0
    ):
        raise ValueError("top_n 必須是正整數")

    weights = {
        "momentum": factor_settings["momentum_weight"],
        "trend": factor_settings["trend_weight"],
        "volatility": factor_settings["volatility_weight"],
    }

    # 即使沒有行情，也先確認權重是否合法。
    rank_factor_candidates([], weights=weights)

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
                raise ValueError("行情日期必須遞增且不可重複")

            previous_date = current_date

            if start <= current_date <= end:
                analysis_dates.add(current_date)

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
        factor_rows = []
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
                factor_rows.append(
                    calculate_factor_row(
                        available[stock_id],
                        as_of,
                        factor_settings=factor_settings,
                    )
                )
            except ValueError as error:
                errors[stock_id] = str(error)

        # 相對排名需要固定的比較母體。
        # 有任何股票無法評估，該日不產生新目標。
        ranked = (
            []
            if errors
            else rank_factor_candidates(
                factor_rows,
                weights=weights,
            )
        )

        history.append(
            {
                "date": as_of,
                "evaluated_count": len(factor_rows),
                "matched_count": len(ranked),
                "candidates": ranked[:top_n],
                "errors": errors,
            }
        )

    return history