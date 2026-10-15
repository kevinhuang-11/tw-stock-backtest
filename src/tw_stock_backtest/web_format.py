"""工作台顯示用的格式化；只影響畫面，不改變保存或匯出的數值。"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

MISSING = "—"

# 內部狀態代碼 → (顯示文字, 標籤樣式)。未列出的代碼原樣顯示為中性樣式。
STATUS = {
    "success": ("完成", "ok"),
    "complete": ("完成", "ok"),
    "partial": ("部分完成", "warn"),
    "failed": ("失敗", "err"),
    "queued": ("排隊中", "info"),
    "running": ("執行中", "info"),
    "cancelled": ("已取消", "neutral"),
    "interrupted": ("已中斷", "warn"),
    "incomplete": ("未完成", "warn"),
    "unreadable": ("無法讀取", "err"),
    "eligible": ("通過", "ok"),
    "passed": ("通過", "ok"),
    "evaluated": ("已評估", "ok"),
    "filtered": ("未通過", "neutral"),
    "excluded": ("已排除", "neutral"),
    "unavailable": ("無法評估", "warn"),
    "no_data": ("無資料", "neutral"),
    "skipped": ("已略過", "neutral"),
    "dry-run": ("僅預覽", "neutral"),
    "pending": ("處理中", "info"),
}

KINDS = {
    "research": "研究排名",
    "backtest": "回測",
    "comparison": "策略比較",
    "period_comparison": "跨期間比較",
    "watchlist": "自選快照",
    "supplemental": "營收與法人更新",
    "watchlist_update": "自選資料更新",
    "unknown": "未知類型",
}


def to_decimal(value):
    """可轉成有限數值時回傳 Decimal，否則回傳 None；不把缺值當成零。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _fixed(number, digits):
    quantum = Decimal(1).scaleb(-digits)
    rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
    # 四捨五入後為零時不顯示負號。
    if rounded == 0:
        rounded = abs(rounded)
    return rounded


def fmt_number(value, digits=2):
    """千分位與固定小數位；缺值或無法解析時顯示破折號。"""
    number = to_decimal(value)
    if number is None:
        return MISSING
    return f"{_fixed(number, digits):,.{digits}f}"


def fmt_int(value):
    return fmt_number(value, 0)


def fmt_percent(value, digits=2, signed=False):
    """把比率（0.0123）顯示為百分比（1.23%）；signed 時正數加上正號。"""
    number = to_decimal(value)
    if number is None:
        return MISSING
    rounded = _fixed(number * 100, digits)
    sign = "+" if signed and rounded > 0 else ""
    text = f"{abs(rounded):,.{digits}f}"
    # 使用數學負號，與等寬數字對齊時寬度一致。
    return ("−" + text if rounded < 0 else sign + text) + "%"


def fmt_signed(value, digits=2):
    number = to_decimal(value)
    if number is None:
        return MISSING
    rounded = _fixed(number, digits)
    text = f"{abs(rounded):,.{digits}f}"
    return "−" + text if rounded < 0 else ("+" + text if rounded > 0 else text)


def trend(value):
    """漲跌方向的樣式名稱：up（紅）、down（綠）、flat。"""
    number = to_decimal(value)
    if number is None or number == 0:
        return "flat"
    return "up" if number > 0 else "down"


def fmt_datetime(value):
    """ISO 時間顯示為台北時間的「日期 時:分」；無法解析時原樣顯示。"""
    if not value:
        return MISSING
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone(ZoneInfo("Asia/Taipei"))
    return moment.strftime("%Y-%m-%d %H:%M")


def status_label(value):
    return STATUS.get(str(value), (MISSING if value in (None, "") else str(value), "neutral"))[0]


def status_kind(value):
    return STATUS.get(str(value), ("", "neutral"))[1]


def kind_label(value):
    return KINDS.get(str(value), str(value) if value else MISSING)


FILTERS = {
    "num": fmt_number,
    "int": fmt_int,
    "pct": fmt_percent,
    "signed": fmt_signed,
    "trend": trend,
    "dt": fmt_datetime,
    "status_label": status_label,
    "status_kind": status_kind,
    "kind_label": kind_label,
}
