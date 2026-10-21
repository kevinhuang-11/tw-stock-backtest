"""截至分析日的還原價：只套用生效日不晚於分析日的公司行動，不使用未來資訊。

每個公司行動有兩個比例：

- 價格因子 = 官方參考價 ÷ 官方前收。生效日以前的開高低收乘上這個因子，跳空因此消失；
  比率型指標（動能、報酬波動、均線交叉、RSI、突破）在事件之間的數值不變。
- 股數比 = 每千股配股或換發後的股數 ÷ 1000。生效日以前的成交量乘上它並四捨五入到整股，
  讓量比跨事件可以比較；只發現金股利時為 1。成交金額不調整。

含現金增資的除權依設定 corporate_actions.rights 處理（PriceSource 讀入時標在每筆事件的
"rights" 欄；沒有標示的視為 refuse）：

- refuse：不跨過生效日還原，序列從生效日重新起算（見 breaks）。
- decline（放棄認購）：價格因子改用減除股利參考價 ÷ 官方前收，只還原現金股利與無償配股；
  現金增資造成的價格下跌留在序列中，和不認購的持有人實際承受的報酬一致。

缺價（None）維持 None，不會被補值；原始資料不會被修改，回傳的是新的列。
"""

from decimal import ROUND_HALF_UP, Decimal

from tw_stock_backtest.data.corporate_actions import CAPITAL_REDUCTION, DIVIDEND, PAR_CHANGE

PRICE_FIELDS = ("open", "high", "low", "close")
THOUSAND = Decimal("1000")
ONE = Decimal("1")

# 現金增資的處理方式（設定 corporate_actions.rights）。
REFUSE = "refuse"
DECLINE = "decline"
RIGHTS_MODES = (REFUSE, DECLINE)


def is_rights_offering(action):
    """含現金增資認購權的除權（原股東每千股可認購股數大於 0）。"""
    return bool(action.get("rights_per_1000")) and action["rights_per_1000"] > 0


def refuses_rights(action):
    """含現金增資、且處理方式是 refuse（沒有標示時的預設）。"""
    return is_rights_offering(action) and action.get("rights", REFUSE) == REFUSE


def ex_dividend_reference(action):
    """減除股利參考價（未捨入）：只扣現金股利、只計無償配股，不含現金增資。

    （前收 − 現金股利）÷（1 ＋ 每千股無償配股 ÷ 1000），與證交所除權息表的同名欄位同一個公式。
    """
    return (action["previous_close"] - action["cash_dividend"]) / (
        ONE + action["stock_dividend_per_1000"] / THOUSAND
    )


def breaks(actions):
    """處理方式為 refuse 的現金增資生效日，由舊到新。

    官方參考價假設依整個現金增資的比例認購，不符合任何實際持有人（不認購的人價格照市價、
    認購的人另外付了認購款），所以不跨過這一天還原價格：序列從生效日重新起算，
    之前的列不用於指標，窗口型指標在事件仍在窗口內時無法評估，遞迴指標重新暖機。
    """
    return sorted({a["effective_date"] for a in actions if refuses_rights(a)})


def events(actions):
    """公司行動轉成 [(生效日, 價格因子, 股數比)]，依生效日排序；同一天的多筆合併相乘。

    refuse 的現金增資不在這裡（見 breaks）；decline 的價格因子用減除股利參考價。
    """
    merged = {}
    for action in actions:
        if refuses_rights(action):
            continue
        if action["previous_close"] is None or action["previous_close"] <= 0:
            raise ValueError(f"{action['stock_id']} {action['effective_date']} 缺少有效的官方前收")
        reference = (
            ex_dividend_reference(action)
            if is_rights_offering(action)
            else action["reference_price"]
        )
        price = reference / action["previous_close"]
        if action["kind"] == DIVIDEND:
            shares = ONE + action["stock_dividend_per_1000"] / THOUSAND
        elif action["kind"] in (CAPITAL_REDUCTION, PAR_CHANGE):
            # 換股率無法確定時（已列入人工確認）只調整價格，成交量維持原狀。
            shares = action["shares_per_1000"] / THOUSAND if action["shares_per_1000"] else ONE
        else:
            raise ValueError(f"未知的公司行動類型：{action['kind']}")
        day = action["effective_date"]
        old_price, old_shares = merged.get(day, (ONE, ONE))
        merged[day] = (old_price * price, old_shares * shares)
    return sorted((day, price, shares) for day, (price, shares) in merged.items())


def _scaled(record, price, shares):
    row = dict(record)
    for field in PRICE_FIELDS:
        if field in row and row[field] is not None:
            row[field] = row[field] * price
    if shares != ONE and isinstance(row.get("volume"), int):
        row["volume"] = int((Decimal(row["volume"]) * shares).to_integral_value(ROUND_HALF_UP))
    return row


def adjust(records, actions, as_of=None):
    """回傳截至 as_of 的還原序列（只含 as_of 以前的列）；as_of 省略時取最後一列的日期。"""
    rows = [r for r in records if as_of is None or r["date"] <= as_of]
    if not rows or not actions:
        return [dict(r) for r in rows]
    cutoff = as_of or rows[-1]["date"]
    applicable = [e for e in events(actions) if e[0] <= cutoff]
    restart = [day for day in breaks(actions) if day <= cutoff]
    if restart:
        rows = [r for r in rows if r["date"] >= restart[-1]]
    result = []
    for record in rows:
        price = shares = ONE
        for day, event_price, event_shares in applicable:
            if record["date"] < day:
                price *= event_price
                shares *= event_shares
        result.append(
            _scaled(record, price, shares) if (price, shares) != (ONE, ONE) else dict(record)
        )
    return result


class AdjustedHistory:
    """逐日累積的還原序列，給逐日計算訊號的引擎使用。

    依日期由舊到新 append；每次 append 前，先把生效日不晚於新列日期的事件套用到既有的列。
    任何時點的內容都等於 adjust(到該日為止的原始列, actions, 該日)，但每個事件只重算一次。
    """

    def __init__(self, actions=()):
        self._events = events(actions) if actions else []
        self._breaks = breaks(actions) if actions else []
        self._next = 0
        self._next_break = 0
        self.rows = []

    def advance(self, as_of):
        while self._next < len(self._events) and self._events[self._next][0] <= as_of:
            day, price, shares = self._events[self._next]
            self.rows = [_scaled(r, price, shares) if r["date"] < day else r for r in self.rows]
            self._next += 1
        while self._next_break < len(self._breaks) and self._breaks[self._next_break] <= as_of:
            day = self._breaks[self._next_break]
            self.rows = [r for r in self.rows if r["date"] >= day]
            self._next_break += 1

    def append(self, record):
        if self.rows and record["date"] <= self.rows[-1]["date"]:
            raise ValueError("還原序列必須依日期遞增加入")
        self.advance(record["date"])
        self.rows.append(dict(record))

    def extend(self, records):
        for record in records:
            self.append(record)
        return self


def point_in_time(records, actions, compute):
    """逐日以截至當日的還原序列計算 compute(rows)，回傳與 records 等長的結果。

    給逐日輸出指標的工具使用（例如單檔均線交叉）：第 i 天的值只用到第 i 天以前生效的
    公司行動，不會因為之後的事件改變。沒有公司行動時等於直接對原始序列逐日計算。
    """
    history = AdjustedHistory(actions)
    values = []
    for record in records:
        history.append(record)
        values.append(compute(history.rows))
    return values
