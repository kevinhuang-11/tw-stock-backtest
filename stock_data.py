from datetime import date
from decimal import Decimal, InvalidOperation

def parse_twse_date(value):
    """將民國日期，例如 114/01/02，轉成 2025-01-02。"""
    roc_year, month, day = value.strip().split("/")

    result = date(
        int(roc_year) + 1911,
        int(month),
        int(day),
    )

    return result.isoformat()

def parse_price(value):
    """將價格字串轉為 Decimal；無成交價格回傳 None。"""
    text = value.strip()

    if text == "--":
        return None

    try:
        price = Decimal(text.replace(",", ""))
    except InvalidOperation as error:
        raise ValueError(f"無效的價格：{value}") from error

    if not price.is_finite() or price < 0:
        raise ValueError(f"價格必須是有限且非負的數值：{value}")

    return price


def parse_volume(value):
    """將成交股數字串轉為非負整數。"""
    text = value.strip().replace(",", "")
    volume = int(text)

    if volume < 0:
        raise ValueError(f"成交股數不能是負數：{value}")

    return volume