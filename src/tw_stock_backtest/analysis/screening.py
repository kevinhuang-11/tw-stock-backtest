from datetime import date
from decimal import Decimal

from tw_stock_backtest.analysis.indicators import simple_moving_average


def evaluate_stock(
    records,
    as_of,
    *,
    short_window,
    long_window,
    volume_window,
    min_volume_ratio,
    momentum_window,
):
    """使用分析日期當天及之前的行情，評估選股條件。"""
    periods = {
        "short_window": short_window,
        "long_window": long_window,
        "volume_window": volume_window,
        "momentum_window": momentum_window,
    }

    for name, value in periods.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise ValueError(f"{name} 必須是正整數")

    if short_window >= long_window:
        raise ValueError("短期均線期間必須小於長期均線期間")

    if (
        not isinstance(min_volume_ratio, Decimal)
        or not min_volume_ratio.is_finite()
        or min_volume_ratio < 0
    ):
        raise ValueError("量比門檻必須是非負且有限的 Decimal")

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

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("行情日期必須遞增且不可重複")

        previous_date = current_date

        if current_date <= target_date:
            history.append(record)

    if not history:
        raise ValueError("分析日期當天及之前沒有行情")

    if history[-1]["date"] != target_date.isoformat():
        raise ValueError("缺少分析日期當天的行情")

    required_records = max(
        short_window,
        long_window,
        volume_window + 1,
        momentum_window + 1,
    )

    if len(history) < required_records:
        raise ValueError(
            f"至少需要 {required_records} 筆行情，"
            f"目前只有 {len(history)} 筆"
        )

    recent = history[-required_records:]
    closes = []
    volumes = []

    for record in recent:
        close = record["close"]
        volume = record["volume"]

        if (
            not isinstance(close, Decimal)
            or not close.is_finite()
            or close <= 0
        ):
            raise ValueError("收盤價必須是正值且有限的 Decimal")

        if (
            isinstance(volume, bool)
            or not isinstance(volume, int)
            or volume < 0
        ):
            raise ValueError("成交量必須是非負整數")

        closes.append(close)
        volumes.append(volume)

    close = closes[-1]

    # 均線包含分析日期當天的收盤價。
    short_ma = simple_moving_average(closes, short_window)[-1]
    long_ma = simple_moving_average(closes, long_window)[-1]

    # 均量只使用當天之前的 volume_window 筆。
    previous_volumes = volumes[-(volume_window + 1):-1]
    previous_avg_volume = (
        Decimal(sum(previous_volumes)) / Decimal(volume_window)
    )

    if previous_avg_volume == 0:
        raise ValueError("前期平均成交量為 0，無法計算量比")

    volume_ratio = Decimal(volumes[-1]) / previous_avg_volume

    # 往前 momentum_window 個行情間隔的收盤價。
    base_close = closes[-(momentum_window + 1)]
    momentum = close / base_close - Decimal("1")

    trend_ok = close > short_ma > long_ma
    volume_ok = volume_ratio > min_volume_ratio

    failed_conditions = []

    if not trend_ok:
        failed_conditions.append(
            f"未符合 Close > SMA{short_window} > SMA{long_window}"
        )

    if not volume_ok:
        failed_conditions.append(
            f"量比未大於 {min_volume_ratio}"
        )

    return {
        "stock_id": stock_id,
        "date": target_date.isoformat(),
        "close": close,
        "short_ma": short_ma,
        "long_ma": long_ma,
        "volume": volumes[-1],
        "previous_avg_volume": previous_avg_volume,
        "volume_ratio": volume_ratio,
        "momentum": momentum,
        "selected": trend_ok and volume_ok,
        "failed_conditions": failed_conditions,
    }