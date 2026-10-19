"""回測中的公司行動帳：持有者在生效日收到的股數與現金。

規則（見 private 提案與 docs/OPERATIONS.md）：

- 權利歸屬：生效日前一個行情日收盤時持有的股數。引擎在生效日開盤成交之前套用，
  所以生效日開盤賣出仍然領得到，開盤買進則領不到。
- 現金股利與減資退還股款在生效日入帳（簡化：實際發放日通常較晚），元以下捨去；
  不扣股利所得稅與二代健保補充保費。
- 配股、減資換股、變更面額取整股；不足一股的部分依面額折發現金（元以下捨去）。
  面額預設 10 元；變更面額時用變更後的面額。
- 現金增資不認購。
- 換股率無法確定（已列入人工確認）時拒絕計算，不自行推定。
"""

from decimal import ROUND_DOWN, Decimal

from tw_stock_backtest.data.corporate_actions import CAPITAL_REDUCTION, DIVIDEND, PAR_CHANGE

PAR_VALUE = Decimal("10")
THOUSAND = Decimal("1000")
ZERO = Decimal("0")


def _yuan(value):
    """元以下捨去。"""
    return value.to_integral_value(rounding=ROUND_DOWN)


def share_ratio(action):
    """每 1 股舊股在生效日後變成幾股（可能是小數）。"""
    if action["kind"] == DIVIDEND:
        return 1 + action["stock_dividend_per_1000"] / THOUSAND
    if action["kind"] in (CAPITAL_REDUCTION, PAR_CHANGE):
        if not action["shares_per_1000"]:
            raise ValueError(
                f"{action['stock_id']} {action['effective_date']} 的換股率無法確定（待人工確認），"
                "無法計算持股變化"
            )
        return action["shares_per_1000"] / THOUSAND
    raise ValueError(f"未知的公司行動類型：{action['kind']}")


def cash_per_share(action):
    """每 1 股舊股收到的現金（股利加上減資退還股款）。"""
    return action["cash_dividend"] + action["cash_return"]


def entitlement(shares, action):
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
    }


class Schedule:
    """依生效日排序的公司行動；due() 依序取出尚未套用、生效日不晚於指定日期的事件。"""

    def __init__(self, actions_by_stock, start):
        self._events = sorted(
            (a["effective_date"], a["stock_id"], a["kind"], a)
            for actions in (actions_by_stock or {}).values()
            for a in actions
            if a["effective_date"] > start
        )
        self._next = 0

    def due(self, day):
        while self._next < len(self._events) and self._events[self._next][0] <= day:
            yield self._events[self._next][3]
            self._next += 1


def apply_due(schedule, day, holdings):
    """在 day 開盤前套用到期的公司行動；更新 holdings，回傳 (入帳現金, 事件明細)。"""
    cash, details = ZERO, []
    for action in schedule.due(day):
        shares = holdings.get(action["stock_id"], 0)
        if not shares:
            continue
        detail = entitlement(shares, action)
        holdings[action["stock_id"]] = detail["shares_after"]
        if not detail["shares_after"]:
            del holdings[action["stock_id"]]
        cash += detail["cash"]
        details.append({**detail, "applied_on": day})
    return cash, details
