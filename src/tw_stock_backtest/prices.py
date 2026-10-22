"""依設定讀取行情：raw 為原始價格；adjusted 為截至分析日的還原價（見 analysis/adjustment.py）。

adjusted 模式會記錄實際用到的公司行動，以及分析期間中沒有下載過公司行動資料的區間；
沒有下載過的區間無法調整，只能如實列出，不能當成「沒有公司行動」。
"""

from tw_stock_backtest.analysis.adjustment import (
    DECLINE,
    REFUSE,
    AdjustedHistory,
    adjust,
    is_rights_offering,
    refuses_rights,
)
from tw_stock_backtest.data.corporate_actions import load_actions, load_coverage, uncovered
from tw_stock_backtest.data.database import load_records

ADJUSTED = "adjusted"
RAW = "raw"
RIGHTS_REASON = (
    "含現金增資：官方參考價假設依整個現金增資比例認購，不符合實際持有人，"
    "還原價不跨過這一天；回測持有跨過這一天時拒絕計算"
)


def price_mode(settings):
    """舊報表的設定沒有這個區段，視為 raw。"""
    return (settings.get("corporate_actions") or {}).get("mode", RAW)


def rights_mode(settings):
    """現金增資的處理方式；舊設定沒有這個欄位，當時只有 refuse。"""
    return (settings.get("corporate_actions") or {}).get("rights", REFUSE)


def report_price_mode(summary=None, settings=None):
    """報表的價格口徑：結果中的 price_adjustment 優先，其次是設定；都沒有的舊報表視為 raw。"""
    recorded = (summary or {}).get("price_adjustment")
    if isinstance(recorded, dict) and recorded.get("mode") in (ADJUSTED, RAW):
        return recorded["mode"]
    return price_mode(settings or {})


def report_price_basis(summary=None, settings=None):
    """報表的價格口徑與現金增資處理方式 (mode, rights)；raw 不調整公司行動，rights 為 None。

    舊報表沒有記錄 rights，當時的行為就是 refuse。
    """
    mode = report_price_mode(summary, settings)
    if mode != ADJUSTED:
        return mode, None
    recorded = (summary or {}).get("price_adjustment")
    if isinstance(recorded, dict) and recorded.get("rights") in (REFUSE, DECLINE):
        return mode, recorded["rights"]
    return mode, rights_mode(settings or {})


def basis_mismatch(left, right):
    """兩個 (mode, rights) 不同時回傳原因（不含結尾），相同時回傳 None。"""
    if left[0] != right[0]:
        return "價格口徑不同（還原價／原始價格）"
    if left[1] != right[1]:
        return "現金增資處理方式不同（拒絕計算／放棄認購）"
    return None


MODE_LABELS = {ADJUSTED: "還原價（adjusted）", RAW: "原始價格（raw）"}
RIGHTS_LABELS = {REFUSE: "現金增資：拒絕計算（refuse）", DECLINE: "現金增資：放棄認購（decline）"}


class PriceSource:
    def __init__(self, settings):
        self.db_path = settings["storage"]["database_path"]
        self.mode = price_mode(settings)
        self.rights = rights_mode(settings)
        self._actions = {}
        self._coverage = {}
        if self.mode == ADJUSTED:
            for action in load_actions(self.db_path):
                # 現金增資標上處理方式，指標、回測與前瞻報酬都依這個欄位。
                if is_rights_offering(action):
                    action = {**action, "rights": self.rights}
                self._actions.setdefault(action["stock_id"], []).append(action)
            self._coverage = load_coverage(self.db_path)
        self._used = {}
        self._gaps = {}
        self._restarts = {}

    def actions(self, stock_id, as_of=None):
        """這檔在 as_of 以前生效的公司行動；raw 模式為空。"""
        return [
            a
            for a in self._actions.get(stock_id, [])
            if as_of is None or a["effective_date"] <= as_of
        ]

    def _track(self, stock_id, first, as_of):
        if self.mode != ADJUSTED or first is None:
            return
        for action in self.actions(stock_id, as_of):
            if action["effective_date"] > first:
                key = (stock_id, action["effective_date"], action["kind"])
                self._used[key] = action
                if refuses_rights(action):
                    self._restarts[key] = action
        gaps = uncovered(self._coverage.get(stock_id, []), first, as_of)
        if gaps:
            known = self._gaps.setdefault(stock_id, [])
            for gap in gaps:
                if gap not in known:
                    known.append(gap)

    def adjust(self, records, stock_id, as_of=None):
        """把已讀出的原始行情轉成截至 as_of 的序列。"""
        rows = [r for r in records if as_of is None or r["date"] <= as_of]
        if self.mode != ADJUSTED or not rows:
            return [dict(r) for r in rows]
        cutoff = as_of or rows[-1]["date"]
        self._track(stock_id, rows[0]["date"], cutoff)
        return adjust(rows, self.actions(stock_id, cutoff), cutoff)

    def load(self, stock_id, start, end, as_of=None):
        """讀取行情並轉成截至 as_of（預設為 end）的序列。"""
        return self.adjust(
            load_records(stock_id, start, end, db_path=self.db_path), stock_id, as_of or end
        )

    def history(self, stock_id, first_date=None, last_date=None):
        """逐日累積的序列；raw 模式不套用任何事件。first/last 用來記錄涵蓋範圍。"""
        if first_date and last_date:
            self._track(stock_id, first_date, last_date)
        return AdjustedHistory(self.actions(stock_id) if self.mode == ADJUSTED else ())

    def actions_by_stock(self, records_by_stock, last):
        """回測引擎用：{股票: last 以前生效的公司行動}，並記錄涵蓋範圍；raw 模式為空字典。

        引擎在每個訊號日只套用該日以前生效的事件（AdjustedHistory），不會提前使用。
        """
        if self.mode != ADJUSTED:
            return {}
        result = {}
        for stock_id, rows in records_by_stock.items():
            if rows:
                self._track(stock_id, rows[0]["date"], last)
            result[stock_id] = self.actions(stock_id, last)
        return result

    def describe_for(self, records_by_stock, last):
        """記錄這些行情到 last 為止會用到的公司行動與涵蓋缺口，回傳摘要。"""
        self.actions_by_stock(records_by_stock, last)
        return self.describe()

    def describe(self):
        """寫入報表的摘要：模式、用到的公司行動、沒有公司行動資料的區間。"""
        return {
            "mode": self.mode,
            # 現金增資的處理方式；raw 模式不調整任何公司行動，不記錄。
            "rights": self.rights if self.mode == ADJUSTED else None,
            "actions": [
                {
                    "stock_id": sid,
                    "effective_date": day,
                    "kind": kind,
                    "previous_close": a["previous_close"],
                    "reference_price": a["reference_price"],
                    "needs_review": a["needs_review"],
                    "sources": a["sources"],
                }
                for (sid, day, kind), a in sorted(self._used.items())
            ],
            "uncovered": {
                sid: [list(gap) for gap in gaps] for sid, gaps in sorted(self._gaps.items())
            },
            # 含現金增資的生效日：還原價不跨過這一天，序列從這一天重新起算。
            "restarts": [
                {"stock_id": sid, "effective_date": day, "reason": RIGHTS_REASON}
                for (sid, day, _), _ in sorted(self._restarts.items())
            ],
        }

    def restart_note(self, stock_id, as_of):
        """這檔在 as_of 以前最近一次含現金增資的生效日說明；沒有時為空字串。"""
        days = [a["effective_date"] for a in self.actions(stock_id, as_of) if refuses_rights(a)]
        if self.mode != ADJUSTED or not days:
            return ""
        return f"（{max(days)} 含現金增資，還原價從這一天重新起算）"


def describe_prices(summary, backtest=False):
    """價格口徑的一行說明，給 CLI 輸出與報表使用；backtest 時另外說明持股與現金的處理。"""
    if summary.get("mode") != ADJUSTED:
        text = "價格：原始價格（未調整除權息、減資與變更面額）"
        return text + "；回測不計股利與股數變動" if backtest else text
    rights = summary.get("rights") or REFUSE
    text = (
        f"價格：截至分析日的還原價（套用 {len(summary['actions'])} 筆公司行動；"
        f"{RIGHTS_LABELS[rights]}）"
    )
    gaps = summary.get("uncovered") or {}
    if gaps:
        text += f"；{len(gaps)} 檔有未下載公司行動資料的期間，這些期間無法調整"
    if backtest:
        text += backtest_note(rights)
    return text


def backtest_note(rights=REFUSE):
    """回測報表與 CLI 共用的註明，依現金增資的處理方式說明。"""
    return BACKTEST_NOTE + (
        RIGHTS_NOTES[rights] if rights in RIGHTS_NOTES else RIGHTS_NOTES[REFUSE]
    )


BACKTEST_NOTE = (
    "。成交與估值使用原始價格；現金股利與減資退還股款於生效日入帳（簡化，實際發放日通常較晚），"
    "未扣股利所得稅與二代健保補充保費；配股與減資換股不足一股依面額折發現金；"
)
RIGHTS_NOTES = {
    REFUSE: "持有跨過含現金增資的除權時拒絕計算",
    DECLINE: (
        "含現金增資的除權一律放棄認購：股數不因認購增加、不付認購款、不賣出認股權，"
        "稀釋損失已反映在市價，另列明細不重複扣除"
    ),
}
RAW_BACKTEST_SCOPE = "未調整除權息、減資與變更面額，報酬不含股利與股數變動"
RIGHTS_SCOPES = {REFUSE: RIGHTS_NOTES[REFUSE], DECLINE: "含現金增資的除權一律放棄認購"}


def backtest_scope(mode, rights=None):
    """回測計入哪些公司行動的一句話（不含句號），CLI 開頭與報表頁共用。"""
    if mode != ADJUSTED:
        return RAW_BACKTEST_SCOPE
    return (
        "現金股利與減資退還股款於生效日入帳，配股、減資換股與變更面額調整股數；"
        f"{RIGHTS_SCOPES.get(rights or REFUSE, RIGHTS_SCOPES[REFUSE])}"
    )


def forward_return_scope(mode, rights=None):
    """前瞻報酬計入哪些公司行動的一句話（不含句號），與 analysis/forward_returns.py 的計算一致。"""
    if mode != ADJUSTED:
        return "前瞻報酬只計價格，不含股利與股數變動"
    if (rights or REFUSE) == DECLINE:
        return "前瞻報酬計入現金股利、減資退還股款與股數變動；含現金增資的除權視為放棄認購"
    return "前瞻報酬計入現金股利、減資退還股款與股數變動；跨過含現金增資除權的樣本不計算"
