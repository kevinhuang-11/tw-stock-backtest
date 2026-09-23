from decimal import Decimal


def simple_moving_average(prices, period):
    """計算 SMA；輸出與輸入等長，資料不足的位置填 None。"""
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError("均線期間必須是正整數")

    if period <= 0:
        raise ValueError("均線期間必須大於 0")

    averages = []
    window_sum = Decimal("0")

    for index, price in enumerate(prices):
        if not isinstance(price, Decimal):
            raise ValueError("價格必須是 Decimal，且不能缺失")

        if not price.is_finite() or price <= 0:
            raise ValueError("價格必須是有限且大於 0 的數值")

        # 加入今天的價格。
        window_sum += price

        # 超過期間長度，就移除視窗外最舊的價格。
        if index >= period:
            window_sum -= prices[index - period]

        if index + 1 < period:
            averages.append(None)
        else:
            averages.append(window_sum / Decimal(period))

    return averages