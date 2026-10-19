"""依設定讀取行情：raw 為原始價格；adjusted 為截至分析日的還原價（見 analysis/adjustment.py）。

adjusted 模式會記錄實際用到的公司行動，以及分析期間中沒有下載過公司行動資料的區間；
沒有下載過的區間無法調整，只能如實列出，不能當成「沒有公司行動」。
"""

from tw_stock_backtest.analysis.adjustment import AdjustedHistory, adjust
from tw_stock_backtest.data.corporate_actions import load_actions, load_coverage, uncovered
from tw_stock_backtest.data.database import load_records

ADJUSTED = "adjusted"
RAW = "raw"


def price_mode(settings):
    """舊報表的設定沒有這個區段，視為 raw。"""
    return (settings.get("corporate_actions") or {}).get("mode", RAW)


def report_price_mode(summary=None, settings=None):
    """報表的價格口徑：結果中的 price_adjustment 優先，其次是設定；都沒有的舊報表視為 raw。"""
    recorded = (summary or {}).get("price_adjustment")
    if isinstance(recorded, dict) and recorded.get("mode") in (ADJUSTED, RAW):
        return recorded["mode"]
    return price_mode(settings or {})


MODE_LABELS = {ADJUSTED: "還原價（adjusted）", RAW: "原始價格（raw）"}


class PriceSource:
    def __init__(self, settings):
        self.db_path = settings["storage"]["database_path"]
        self.mode = price_mode(settings)
        self._actions = {}
        self._coverage = {}
        if self.mode == ADJUSTED:
            for action in load_actions(self.db_path):
                self._actions.setdefault(action["stock_id"], []).append(action)
            self._coverage = load_coverage(self.db_path)
        self._used = {}
        self._gaps = {}

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
        }


def describe_prices(summary, backtest=False):
    """價格口徑的一行說明，給 CLI 輸出與報表使用；backtest 時另外說明持股與現金的處理。"""
    if summary.get("mode") != ADJUSTED:
        text = "價格：原始價格（未調整除權息、減資與變更面額）"
        return text + "；回測不計股利與股數變動" if backtest else text
    text = f"價格：截至分析日的還原價（套用 {len(summary['actions'])} 筆公司行動）"
    gaps = summary.get("uncovered") or {}
    if gaps:
        text += f"；{len(gaps)} 檔有未下載公司行動資料的期間，這些期間無法調整"
    if backtest:
        text += BACKTEST_NOTE
    return text


# 回測報表與 CLI 共用的註明。
BACKTEST_NOTE = (
    "。成交與估值使用原始價格；現金股利與減資退還股款於生效日入帳（簡化，實際發放日通常較晚），"
    "未扣股利所得稅與二代健保補充保費；配股與減資換股不足一股依面額折發現金；現金增資不認購"
)
