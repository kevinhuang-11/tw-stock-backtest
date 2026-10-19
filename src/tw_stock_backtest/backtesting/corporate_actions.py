"""回測中的公司行動帳：持有者在生效日收到的股數與現金。

規則（見 private 提案與 docs/OPERATIONS.md）：

- 權利歸屬：生效日前一個行情日收盤時持有的股數。引擎在生效日開盤成交之前套用，
  所以生效日開盤賣出仍然領得到，開盤買進則領不到。
- 現金股利與減資退還股款在生效日入帳（簡化：實際發放日通常較晚），元以下捨去；
  不扣股利所得稅與二代健保補充保費。
- 配股、減資換股、變更面額取整股；不足一股的部分依面額折發現金（元以下捨去）。
  面額預設 10 元；變更面額時用變更後的面額。
- 現金增資依設定 corporate_actions.rights：
  - refuse（預設）：持有跨過含現金增資的生效日時拒絕計算。
  - decline（放棄認購）：股數不因認購增加、不付認購款，認股權不賣出、直接放棄；
    同一筆的現金股利與無償配股照常計入。放棄的代價（稀釋損失）已反映在除權後的市價，
    不另外扣除，只在明細列出：持股 ×（1 ＋ 配股率）×（減除股利參考價 − 官方參考價）。
- 換股率無法確定（已列入人工確認）時拒絕計算，不自行推定。
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from decimal import ROUND_DOWN, Decimal
from typing import Any

from tw_stock_backtest.analysis.adjustment import (
    ex_dividend_reference,
    is_rights_offering,
    refuses_rights,
)
from tw_stock_backtest.data.corporate_actions import CAPITAL_REDUCTION, DIVIDEND, PAR_CHANGE
from tw_stock_backtest.typedefs import Action, Row

PAR_VALUE = Decimal("10")
THOUSAND = Decimal("1000")
ZERO = Decimal("0")


def _yuan(value: Decimal) -> Decimal:
    """元以下捨去。"""
    return value.to_integral_value(rounding=ROUND_DOWN)


def share_ratio(action: Action) -> Decimal:
    """每 1 股舊股在生效日後變成幾股（可能是小數）。"""
    ratio: Decimal
    if action["kind"] == DIVIDEND:
        ratio = 1 + action["stock_dividend_per_1000"] / THOUSAND
        return ratio
    if action["kind"] in (CAPITAL_REDUCTION, PAR_CHANGE):
        if not action["shares_per_1000"]:
            raise ValueError(
                f"{action['stock_id']} {action['effective_date']} 的換股率無法確定（待人工確認），"
                "無法計算持股變化"
            )
        ratio = action["shares_per_1000"] / THOUSAND
        return ratio
    raise ValueError(f"未知的公司行動類型：{action['kind']}")


def cash_per_share(action: Action) -> Decimal:
    """每 1 股舊股收到的現金（股利加上減資退還股款）。"""
    cash: Decimal = action["cash_dividend"] + action["cash_return"]
    return cash


def entitlement(shares: int, action: Action) -> Row:
    """持有 shares 股（整數）時，這筆公司行動帶來的變化。"""
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
        raise ValueError("持股必須是非負整數")
    ratio = share_ratio(action)
    exact = Decimal(shares) * ratio
    after = int(exact.to_integral_value(rounding=ROUND_DOWN))
    par = PAR_VALUE / ratio if action["kind"] == PAR_CHANGE else PAR_VALUE
    dividend = _yuan(Decimal(shares) * action["cash_dividend"])
    refund = _yuan(Decimal(shares) * action["cash_return"])
    fraction = _yuan((exact - after) * par)
    return {
        "stock_id": action["stock_id"],
        "date": action["effective_date"],
        "kind": action["kind"],
        "shares_before": shares,
        "shares_after": after,
        "cash_dividend": dividend,
        "cash_return": refund,
        "fraction_cash": fraction,
        "cash": dividend + refund + fraction,
        "needs_review": action.get("needs_review", False),
        **(
            declined_rights(shares, action)
            if is_rights_offering(action) and not refuses_rights(action)
            else {}
        ),
    }


def declined_rights(shares: int, action: Action) -> dict[str, Any]:
    """放棄認購的明細：可認購股數、認購價與稀釋損失（元以下捨去；已反映在市價，不另外扣除）。

    稀釋損失 = 持股 ×（1 ＋ 配股率）×（減除股利參考價 − 官方參考價），也就是除權後部位
    依官方參考價計算，比沒有現金增資時少的價值；認購價高於參考價時為負值。
    """
    stock_ratio = 1 + action["stock_dividend_per_1000"] / THOUSAND
    loss = (
        Decimal(shares) * stock_ratio * (ex_dividend_reference(action) - action["reference_price"])
    )
    return {
        "rights": "decline",
        "rights_shares": Decimal(shares) * action["rights_per_1000"] / THOUSAND,
        "rights_price": action["rights_price"],
        "ex_dividend_reference": ex_dividend_reference(action),
        "reference_price": action["reference_price"],
        "dilution_loss": _yuan(loss),
    }


def declined_text(event: Mapping[str, Any]) -> str:
    """CLI 輸出用：放棄認購的說明；不是放棄認購的事件回傳空字串。"""
    if event.get("rights") != "decline":
        return ""
    return (
        f"｜放棄認購 {Decimal(str(event['rights_shares'])).normalize():f} 股"
        f"（認購價 {Decimal(str(event['rights_price'])).normalize():f} 元），"
        f"稀釋損失 {event['dilution_loss']} 元（已反映在市價，不另外扣除）"
    )


def dilution_total(events: Iterable[Mapping[str, Any]]) -> Decimal:
    """放棄認購的稀釋損失合計。"""
    return sum(
        (Decimal(str(e["dilution_loss"])) for e in events if e.get("rights") == "decline"), ZERO
    )


class RightsOfferingHeld(ValueError):
    """持有跨過含現金增資的生效日；這段報酬不計算。"""

    def __init__(self, action: Action) -> None:
        super().__init__(
            f"持有 {action['stock_id']} 跨過 {action['effective_date']} 的現金增資除權"
            f"（原股東每千股可認購 {action['rights_per_1000'].normalize():f} 股、"
            f"認購價 {action['rights_price'].normalize():f} 元）。本專案不模擬認購或轉讓認股權，"
            "無法可靠計算這段報酬；請排除這檔、調整期間避開這一天、設定"
            ' corporate_actions.rights = "decline"（放棄認購），或改用 raw 模式（不計任何公司行動）'
        )
        self.action = action


class Schedule:
    """依生效日排序的公司行動；due() 依序取出尚未套用、生效日不晚於指定日期的事件。"""

    def __init__(self, actions_by_stock: Mapping[str, Sequence[Action]] | None, start: str) -> None:
        self._events = sorted(
            (a["effective_date"], a["stock_id"], a["kind"], a)
            for actions in (actions_by_stock or {}).values()
            for a in actions
            if a["effective_date"] > start
        )
        self._next = 0

    def due(self, day: str) -> Iterator[Action]:
        while self._next < len(self._events) and self._events[self._next][0] <= day:
            yield self._events[self._next][3]
            self._next += 1


def apply_due(schedule: Schedule, day: str, holdings: dict[str, int]) -> tuple[Decimal, list[Row]]:
    """在 day 開盤前套用到期的公司行動；更新 holdings，回傳 (入帳現金, 事件明細)。"""
    cash, details = ZERO, []
    for action in schedule.due(day):
        shares = holdings.get(action["stock_id"], 0)
        if not shares:
            continue
        if refuses_rights(action):
            raise RightsOfferingHeld(action)
        detail = entitlement(shares, action)
        holdings[action["stock_id"]] = detail["shares_after"]
        if not detail["shares_after"]:
            del holdings[action["stock_id"]]
        cash += detail["cash"]
        details.append({**detail, "applied_on": day})
    return cash, details
