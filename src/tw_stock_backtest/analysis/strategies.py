def moving_average_crossover(short_ma, long_ma):
    """根據相鄰兩天的均線，產生 BUY、SELL 或 NONE。"""
    if len(short_ma) != len(long_ma):
        raise ValueError("短期與長期均線的資料長度必須相同")

    signals = ["NONE"] * len(short_ma)

    for index in range(1, len(short_ma)):
        previous_short = short_ma[index - 1]
        previous_long = long_ma[index - 1]
        current_short = short_ma[index]
        current_long = long_ma[index]

        values = (
            previous_short,
            previous_long,
            current_short,
            current_long,
        )

        # 任一均線尚未形成，就不能比較交叉。
        if any(value is None for value in values):
            continue

        if (
            previous_short <= previous_long
            and current_short > current_long
        ):
            signals[index] = "BUY"

        elif (
            previous_short >= previous_long
            and current_short < current_long
        ):
            signals[index] = "SELL"

    return signals