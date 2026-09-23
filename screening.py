from datetime import date
from decimal import Decimal

from indicators import simple_moving_average


def evaluate_stock(records, as_of):
    """以指定日期評估一支股票；只使用該日以前的資料。"""
    target_date = date.fromisoformat(as_of)

    if not records:
        raise ValueError("沒有行情資料")

    stock_id = records[0]["stock_id"]
    history = []
    previous_date = None

    for record in records:
        current_date = date.fromisoformat(record["date"])

        if record["stock_id"] != stock_id:
            raise ValueError("一次只能評估一支股票")

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("日期必須由舊到新，且不能重複")

        previous_date = current_date

        # 即使傳入較新的資料，也不拿來分析過去。
        if current_date <= target_date:
            history.append(record)

    if not history:
        raise ValueError("指定日期以前沒有行情")

    if history[-1]["date"] != target_date.isoformat():
        raise ValueError("缺少指定分析日行情，不能使用舊資料代替")

    if len(history) < 21:
        raise ValueError("至少需要 21 筆行情")

    # 本版只需要最近 21 筆。
    recent = history[-21:]
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
            raise ValueError(f"{record['date']} 收盤價無效")

        if (
            isinstance(volume, bool)
            or not isinstance(volume, int)
            or volume < 0
        ):
            raise ValueError(f"{record['date']} 成交股數無效")

        closes.append(close)
        volumes.append(volume)

    close = closes[-1]
    sma5 = simple_moving_average(closes, 5)[-1]
    sma20 = simple_moving_average(closes, 20)[-1]

    previous_avg_volume = (
        Decimal(sum(volumes[:-1])) / Decimal("20")
    )

    if previous_avg_volume == 0:
        raise ValueError("前 20 筆平均成交股數為 0，無法計算量比")

    volume_ratio = Decimal(volumes[-1]) / previous_avg_volume
    momentum20 = close / closes[0] - Decimal("1")

    trend_ok = close > sma5 > sma20
    volume_ok = volume_ratio > Decimal("1")

    failed_conditions = []

    if not trend_ok:
        failed_conditions.append("未符合 Close > SMA5 > SMA20")

    if not volume_ok:
        failed_conditions.append("當日量未高於前20筆均量")

    return {
        "stock_id": stock_id,
        "date": target_date.isoformat(),
        "close": close,
        "sma5": sma5,
        "sma20": sma20,
        "volume": volumes[-1],
        "previous_avg_volume": previous_avg_volume,
        "volume_ratio": volume_ratio,
        "momentum20": momentum20,
        "selected": trend_ok and volume_ok,
        "failed_conditions": failed_conditions,
    }