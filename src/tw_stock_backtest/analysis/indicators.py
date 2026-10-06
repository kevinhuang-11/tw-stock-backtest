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

def _period(period):
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError('期間必須為正整數')


def _finite(values, positive=False):
    if any(not isinstance(v, Decimal) or not v.is_finite() or (positive and v <= 0) for v in values):
        raise ValueError('輸入必須為有限 Decimal；價格須正值，不可缺值')


def exponential_moving_average(values, period):
    """EMA: first period values' SMA seed; alpha=2/(period+1). Earlier entries None.

    Accept signed finite Decimal series (also used for MACD signal), no gap removal.
    """
    _period(period)
    _finite(values)
    result = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period], Decimal(0)) / period
    result[period - 1] = current
    alpha = Decimal(2) / (period + 1)
    for i in range(period, len(values)):
        current += alpha * (values[i] - current)
        result[i] = current
    return result


def relative_strength_index(prices, period=14):
    """Wilder RSI: N changes seed average gains/losses; then (old*(N-1)+new)/N.

    First output at N+1 prices; flat=50, gains-only=100, losses-only=0.
    Validate whole supplied prefix: recursive indicators retain all earlier history.
    """
    _period(period)
    _finite(prices, positive=True)
    result = [None] * len(prices)
    if len(prices) <= period:
        return result
    changes = [b - a for a, b in zip(prices, prices[1:])]
    gains = [max(c, Decimal(0)) for c in changes]
    losses = [max(-c, Decimal(0)) for c in changes]
    gain = sum(gains[:period], Decimal(0)) / period
    loss = sum(losses[:period], Decimal(0)) / period
    def value():
        return Decimal(50) if gain == loss == 0 else Decimal(100) if loss == 0 else Decimal(100) * gain / (gain + loss)
    result[period] = value()
    for i in range(period, len(changes)):
        gain = (gain * (period - 1) + gains[i]) / period
        loss = (loss * (period - 1) + losses[i]) / period
        result[i + 1] = value()
    return result


def moving_average_convergence_divergence(prices, fast=12, slow=26, signal=9):
    """SMA-seeded EMA(fast)-EMA(slow); signal seeds first M available MACD values.

    MACD available after slow prices; signal/histogram after slow+signal-1.
    Histogram = MACD - signal (no 2x multiplier). Entire prefix must be valid.
    """
    for p in (fast, slow, signal):
        _period(p)
    if fast >= slow:
        raise ValueError('MACD fast 必須小於 slow')
    _finite(prices, positive=True)
    a, b = exponential_moving_average(prices, fast), exponential_moving_average(prices, slow)
    values = [a[i] - b[i] for i in range(slow - 1, len(prices))]
    signals = exponential_moving_average(values, signal)
    result = [{'macd': None, 'signal': None, 'histogram': None} for _ in prices]
    for offset, value in enumerate(values):
        line = signals[offset]
        result[slow - 1 + offset] = {'macd': value, 'signal': line,
                                    'histogram': value - line if line is not None else None}
    return result


def previous_high_breakout(records, period):
    """Today's close > maximum high of preceding N rows; exclude today's high.

    Needs N+1 rows, no filling or removing missing rows. Only used window validated.
    """
    _period(period)
    if len(records) < period + 1:
        raise ValueError(f'突破需要 {period + 1} 筆行情')
    highs = [r['high'] for r in records[-period - 1:-1]]
    close = records[-1]['close']
    _finite(highs + [close], positive=True)
    threshold = max(highs)
    return {'close': close, 'threshold': threshold, 'passed': close > threshold}
