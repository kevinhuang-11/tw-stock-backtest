"""核心模組共用的型別別名（mypy 用；執行時不做任何檢查）。

行情列、公司行動與回測紀錄都是以字串為鍵的字典，欄位會隨功能增加，所以以別名標示用途，
值的型別仍是 Any；結構固定的（例如一筆成交的金額）才用 TypedDict。金額與價格一律是 Decimal。
"""

from decimal import Decimal
from typing import Any, TypedDict

# 一列行情：stock_prices 的欄位（stock_id、date、open … trade_count）；
# 經 prepare_market_records 後另有 tradable、valuation_close、valuation_price_date、market_status。
Record = dict[str, Any]

# 一筆公司行動：data/corporate_actions.COLUMNS 的欄位；PriceSource 讀入時可能加上 rights。
Action = dict[str, Any]

# 已確認停牌：(股票, 日期) → {"reason", "source", "sources"}。
Halts = dict[tuple[str, str], dict[str, Any]]

# 已確認終止上市：股票 → {"announced", "effective", "kind", "reason", "consideration", "sources"}。
Delistings = dict[str, dict[str, Any]]

# 回測用的行情：股票 → 日期 → 準備後的行情列。
Market = dict[str, dict[str, Record]]

# 回測輸出的一筆紀錄（成交、未成交、事件、資產曲線的一天等）。
Row = dict[str, Any]


class Transaction(TypedDict):
    """一筆成交的股款、手續費、交易稅與現金變動（買進為負）。"""

    amount: Decimal
    commission: Decimal
    tax: Decimal
    cash_change: Decimal
