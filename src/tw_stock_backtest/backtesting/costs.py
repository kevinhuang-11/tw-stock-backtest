from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


@dataclass(frozen=True)
class CostSettings:
    """交易成本設定；預設值為本專案的基準模擬情境。"""
    commission_rate: Decimal = Decimal("0.001425")
    commission_discount: Decimal = Decimal("1")
    minimum_commission: Decimal = Decimal("20")
    sell_tax_rate: Decimal = Decimal("0.003")

    commission_rounding: str = ROUND_DOWN
    tax_rounding: str = ROUND_DOWN

    def __post_init__(self):
        rates = (
            ("手續費率", self.commission_rate),
            ("手續費折扣倍率", self.commission_discount),
            ("賣出交易稅率", self.sell_tax_rate),
        )

        for name, value in rates:
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or not Decimal("0") <= value <= Decimal("1")
            ):
                raise ValueError(f"{name}必須是 0 到 1 的有限 Decimal")

        minimum = self.minimum_commission

        if (
            not isinstance(minimum, Decimal)
            or not minimum.is_finite()
            or minimum < 0
            or minimum != minimum.to_integral_value()
        ):
            raise ValueError("最低手續費必須是非負整數元的 Decimal")

        for rounding in (
            self.commission_rounding,
            self.tax_rounding,
        ):
            if rounding not in (ROUND_DOWN, ROUND_HALF_UP):
                raise ValueError("目前只支援無條件捨去或四捨五入")


def calculate_transaction(price, quantity, side, settings):
    """計算一筆成交的股款、手續費、交易稅與現金變動。"""
    if (
        not isinstance(price, Decimal)
        or not price.is_finite()
        or price <= 0
    ):
        raise ValueError("成交價格必須是大於 0 的有限 Decimal")

    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
    ):
        raise ValueError("成交股數必須是正整數")

    if side not in ("BUY", "SELL"):
        raise ValueError("交易方向必須是 BUY 或 SELL")

    amount = price * quantity

    raw_commission = (
        amount
        * settings.commission_rate
        * settings.commission_discount
    )

    commission = raw_commission.quantize(
        Decimal("1"),
        rounding=settings.commission_rounding,
    )
    commission = max(commission, settings.minimum_commission)

    tax = Decimal("0")

    if side == "SELL":
        tax = (amount * settings.sell_tax_rate).quantize(
            Decimal("1"),
            rounding=settings.tax_rounding,
        )

    if side == "BUY":
        cash_change = -(amount + commission)
    else:
        cash_change = amount - commission - tax

    return {
        "amount": amount,
        "commission": commission,
        "tax": tax,
        "cash_change": cash_change,
    }

def calculate_buy_quantity(price, budget, settings):
    """找出預算內可買的最大整數股數，預算包含手續費。"""
    if (
        not isinstance(price, Decimal)
        or not price.is_finite()
        or price <= 0
    ):
        raise ValueError("價格必須是大於零的有限 Decimal")

    if (
        not isinstance(budget, Decimal)
        or not budget.is_finite()
        or budget < 0
    ):
        raise ValueError("預算必須是非負的有限 Decimal")

    if not isinstance(settings, CostSettings):
        raise ValueError("settings 必須是 CostSettings")

    # 完全不計手續費時，最多能買的股數。
    low = 0
    high = int(budget // price)

    # 二分搜尋：找出含手續費後，仍在預算內的最大股數。
    while low < high:
        middle = (low + high + 1) // 2

        transaction = calculate_transaction(
            price,
            middle,
            "BUY",
            settings,
        )

        required_cash = -transaction["cash_change"]

        if required_cash <= budget:
            low = middle
        else:
            high = middle - 1

    return low

def apply_slippage(price, action, slippage_rate=Decimal("0")):
    """
    依固定比例模擬不利滑價。

    BUY：成交價提高。
    SELL：成交價降低。
    保留 Decimal 精度，不在此處取整或對齊價格跳動單位。
    """
    if (
        not isinstance(price, Decimal)
        or not price.is_finite()
        or price <= 0
    ):
        raise ValueError("價格必須是大於零的有限 Decimal")

    if action not in ("BUY", "SELL"):
        raise ValueError("交易方向必須是 BUY 或 SELL")

    if (
        not isinstance(slippage_rate, Decimal)
        or not slippage_rate.is_finite()
        or not Decimal("0") <= slippage_rate < Decimal("1")
    ):
        raise ValueError(
            "滑價率必須是介於 0（含）與 1（不含）的有限 Decimal"
        )

    if action == "BUY":
        return price * (Decimal("1") + slippage_rate)

    return price * (Decimal("1") - slippage_rate)

