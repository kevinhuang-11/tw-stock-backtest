"""公司行動（除權息、減資、變更面額）的解析與儲存。只處理資料，不參與任何計算。

來源都是證交所公開端點，與行情下載同一來源：

- 除權除息計算結果表 TWT49U 與詳細資料 TWT49UDetail；
- 股票減資恢復買賣參考價格 TWTAUU 與詳細資料 TWTAVUDetail；
- 變更股票面額恢復買賣參考價格 TWTB8U。

數值一律保存為 Decimal 字串，與行情相同。官方公告的「前收」與「參考價」是日後計算
調整因子的依據；各項金額另外保存，供回測調整持股與現金。解析時以官方公式反推參考價，
對不上、或來源本身含糊（例如減資表注意事項中的併案股利）時標記為待人工確認，不自行推定。
"""

import json
import re
import sqlite3
from contextlib import closing
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from tw_stock_backtest.data.database import migrate_database

DIVIDEND = "dividend"
CAPITAL_REDUCTION = "capital_reduction"
PAR_CHANGE = "par_change"
KINDS = (DIVIDEND, CAPITAL_REDUCTION, PAR_CHANGE)

TWSE = "https://www.twse.com.tw"
ZERO = Decimal("0")
THOUSAND = Decimal("1000")
# 官方參考價取到小數第二位；反推時允許的誤差。
TOLERANCE = Decimal("0.01")
# 變更面額時常見的新面額（元）；換股率 = 10 ÷ 新面額。
PAR_VALUES = ("5", "2.5", "2", "1", "0.5", "0.25", "0.2", "0.1")

COLUMNS = (
    "stock_id",
    "effective_date",
    "kind",
    "previous_close",
    "reference_price",
    "cash_dividend",
    "stock_dividend_per_1000",
    "shares_per_1000",
    "cash_return",
    "rights_per_1000",
    "rights_price",
    "halt_start",
    "needs_review",
    "review_notes",
    "sources",
    "raw",
    "fetched_at",
)
DECIMAL_COLUMNS = (
    "previous_close",
    "reference_price",
    "cash_dividend",
    "stock_dividend_per_1000",
    "shares_per_1000",
    "cash_return",
    "rights_per_1000",
    "rights_price",
)


# ---------- 欄位與數值 ----------


def roc_date(text):
    """民國日期（115年03月17日、115/09/29）轉成 ISO 字串。"""
    numbers = re.findall(r"\d+", text or "")
    if len(numbers) != 3:
        raise ValueError(f"日期格式無法辨識：{text!r}")
    year, month, day = (int(n) for n in numbers)
    return date(year + 1911, month, day).isoformat()


def amount(text):
    """「1,845.00」「6.000035 元／股」「30 股」轉成 Decimal；空白或 -- 視為 None。"""
    cleaned = (text or "").replace(",", "").strip()
    match = re.match(r"^-?\d+(\.\d+)?", cleaned)
    if not match:
        if cleaned in ("", "--", "-"):
            return None
        raise ValueError(f"數值格式無法辨識：{text!r}")
    try:
        return Decimal(match.group(0))
    except InvalidOperation as error:
        raise ValueError(f"數值格式無法辨識：{text!r}") from error


def _key(field):
    return re.sub(r"[\s:：]", "", field)


def records(payload):
    """把 {"fields", "data"} 轉成以去除空白與冒號後的欄位名稱為鍵的列。"""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        raise ValueError(
            f"公司行動查詢未成功：{payload.get('stat') if isinstance(payload, dict) else payload!r}"
        )
    fields = [_key(field) for field in payload.get("fields") or []]
    rows = []
    for row in payload.get("data") or []:
        if len(row) != len(fields):
            raise ValueError("公司行動欄位數量與資料數量不一致")
        rows.append(dict(zip(fields, row, strict=True)))
    return rows


def _field(row, *fragments):
    """依欄位名稱片段取值；必須剛好對到一個欄位，避免改版後默默取錯欄。"""
    keys = [key for key in row if all(fragment in key for fragment in fragments)]
    if len(keys) != 1:
        raise ValueError(f"找不到唯一的欄位：{'、'.join(fragments)}（候選 {keys}）")
    return row[keys[0]]


def _stock(row):
    return _field(row, "股票代號").strip()


def _close_enough(computed, official):
    return abs(computed - official) < TOLERANCE


def _new(stock_id, effective_date, kind, previous_close, reference_price, sources, raw):
    return {
        "stock_id": stock_id,
        "effective_date": effective_date,
        "kind": kind,
        "previous_close": previous_close,
        "reference_price": reference_price,
        "cash_dividend": ZERO,
        "stock_dividend_per_1000": ZERO,
        "shares_per_1000": None,
        "cash_return": ZERO,
        "rights_per_1000": ZERO,
        "rights_price": ZERO,
        "halt_start": None,
        "needs_review": False,
        "review_notes": [],
        "sources": list(sources),
        "raw": raw,
    }


def _review(action, note):
    action["needs_review"] = True
    action["review_notes"].append(note)


# ---------- 除權除息 ----------


def dividend_rows(payload, stocks):
    """除權除息表中屬於 stocks 的列，回傳 (列, 詳細資料鍵 (代號, YYYYMMDD), 是否需要詳細資料)。

    權／息欄只有「息」時，權值＋息值就是每股現金股利，不需要詳細資料；
    含「權」（配股或現金增資）時才需要查詳細資料。
    """
    wanted = set(stocks)
    result = []
    for row in records(payload):
        stock_id = _stock(row)
        if stock_id not in wanted:
            continue
        detail_stock, detail_date = _field(row, "詳細資料").replace(" ", "").split(",")[:2]
        result.append((row, (detail_stock, detail_date), "權" in _field(row, "權/息")))
    return result


def parse_dividend(row, detail=None, *, list_url):
    """一列除權除息資料（及必要時的詳細資料）轉成公司行動。"""
    stock_id = _stock(row)
    effective_date = roc_date(_field(row, "資料日期"))
    previous_close = amount(_field(row, "除權息前收盤價"))
    reference_price = amount(_field(row, "除權息參考價"))
    flag = _field(row, "權/息")
    detail_stock, detail_date = _field(row, "詳細資料").replace(" ", "").split(",")[:2]
    detail_url = (
        f"{TWSE}/rwd/zh/exRight/TWT49UDetail?STK_NO={detail_stock}&T1={detail_date}&response=json"
    )
    action = _new(
        stock_id,
        effective_date,
        DIVIDEND,
        previous_close,
        reference_price,
        [list_url],
        {"list": row},
    )

    if "權" not in flag:
        action["cash_dividend"] = amount(_field(row, "權值", "息值"))
    else:
        if detail is None:
            raise ValueError(f"{stock_id} {effective_date} 含除權，需要詳細資料")
        values = records(detail)
        if len(values) != 1 or _stock(values[0]) != stock_id:
            raise ValueError(f"{stock_id} {effective_date} 的除權息詳細資料不符")
        values = values[0]
        action["cash_dividend"] = amount(_field(values, "每股配發現金股利")) or ZERO
        action["stock_dividend_per_1000"] = amount(_field(values, "每千股無償配股")) or ZERO
        action["rights_per_1000"] = amount(_field(values, "按股東持股比例每千股認購")) or ZERO
        action["rights_price"] = amount(_field(values, "每股認購金額")) or ZERO
        action["sources"].append(detail_url.replace("&response=json", ""))
        action["raw"]["detail"] = values

    # 參考價 =（前收 − 現金股利 ＋ 認購價 × 每千股認購 ÷ 1000）÷（1 ＋ 配股率 ＋ 認購率）
    stock_ratio = action["stock_dividend_per_1000"] / THOUSAND
    rights_ratio = action["rights_per_1000"] / THOUSAND
    computed = (
        previous_close - action["cash_dividend"] + action["rights_price"] * rights_ratio
    ) / (1 + stock_ratio + rights_ratio)
    if not _close_enough(computed, reference_price):
        _review(
            action,
            f"依公告數值反推參考價為 {computed.quantize(TOLERANCE, ROUND_DOWN)}，與官方 {reference_price} 不符",
        )
    if action["rights_per_1000"] > 0:
        _review(action, "含現金增資認購權；本專案不認購，只調整價格序列")
    return action


# ---------- 減資 ----------

COMBINED_NOTE = re.compile(
    r"(\d{4})\s+\S+\s+除息併案辦理減資，原股[毎每]壹股配發現金股利([\d.]+)元"
)


def reduction_rows(payload, stocks):
    """減資表中屬於 stocks 的列與詳細資料鍵 (代號, FILE_DATE)，以及注意事項中的併案股利 {代號: 金額}。"""
    wanted = set(stocks)
    result = []
    for row in records(payload):
        stock_id = _stock(row)
        if stock_id in wanted:
            detail_stock, detail_date = _field(row, "詳細資料").replace(" ", "").split(",")[:2]
            result.append((row, (detail_stock, detail_date)))
    combined = {}
    for note in payload.get("notes") or []:
        match = COMBINED_NOTE.search(note)
        if match and match.group(1) in wanted:
            combined[match.group(1)] = Decimal(match.group(2))
    return result, combined


def parse_reduction(row, detail, *, list_url, combined_dividend=None):
    """一列減資資料與其詳細資料轉成公司行動；生效日是恢復買賣日。

    combined_dividend：減資表注意事項中「除息併案辦理減資」的每股股利。注意事項是針對
    整段查詢期間，並未指明是哪一次減資，所以只有含股利的公式與官方參考價吻合時才歸給這一次，
    並一律列入人工確認。
    """
    stock_id = _stock(row)
    effective_date = roc_date(_field(row, "恢復買賣日期"))
    previous_close = amount(_field(row, "停止買賣前收盤價格"))
    reference_price = amount(_field(row, "恢復買賣參考價"))
    detail_stock, detail_date = _field(row, "詳細資料").replace(" ", "").split(",")[:2]
    detail_url = f"{TWSE}/zh/announcement/reduction/twtavu-detail.html?{detail_stock},{detail_date}"
    action = _new(
        stock_id,
        effective_date,
        CAPITAL_REDUCTION,
        previous_close,
        reference_price,
        [list_url, detail_url],
        {"list": row},
    )

    values = records(detail)
    if len(values) != 1 or _stock(values[0]) != stock_id:
        raise ValueError(f"{stock_id} {effective_date} 的減資詳細資料不符")
    values = values[0]
    action["raw"]["detail"] = values
    action["raw"]["reason"] = _field(row, "減資原因")
    action["halt_start"] = roc_date(_field(values, "停止買賣日期"))
    action["shares_per_1000"] = amount(_field(values, "每壹仟股換發新股票"))
    action["cash_return"] = amount(_field(values, "每股退還股款")) or ZERO
    detail_dividend = amount(_field(values, "原股每股配發現金股利")) or ZERO
    action["rights_per_1000"] = amount(_field(values, "按股東持股比例每千股認購")) or ZERO
    action["rights_price"] = amount(_field(values, "每股認購金額")) or ZERO
    if not action["shares_per_1000"]:
        raise ValueError(f"{stock_id} {effective_date} 的減資換股率無效")

    ratio = action["shares_per_1000"] / THOUSAND
    without = (previous_close - action["cash_return"]) / ratio
    candidates = {dividend for dividend in (detail_dividend, combined_dividend) if dividend}

    if candidates:
        dividend = max(candidates)
        with_dividend = (previous_close - action["cash_return"] - dividend) / ratio
        if _close_enough(with_dividend, reference_price):
            action["cash_dividend"] = dividend
            _review(
                action,
                f"除息併案辦理減資，每股現金股利 {dividend} 元；含股利的公式與官方參考價吻合",
            )
        elif _close_enough(without, reference_price):
            _review(
                action,
                f"減資表注意事項列出併案股利每股 {dividend} 元，但官方參考價不含這筆股利，"
                "可能屬於同一查詢期間的另一次減資；股利未計入這一次",
            )
        else:
            _review(action, f"併案股利每股 {dividend} 元；含或不含股利的公式都與官方參考價不符")
    elif not _close_enough(without, reference_price):
        _review(
            action,
            f"依公告數值反推參考價為 {without.quantize(TOLERANCE, ROUND_DOWN)}，與官方 {reference_price} 不符",
        )
    if action["rights_per_1000"] > 0:
        _review(action, "減資並辦現金增資；本專案不認購，只調整價格序列")
    return action


# ---------- 變更面額 ----------


def parse_par_change(row, *, list_url):
    """一列變更面額資料轉成公司行動；換股率由前收與參考價反推，只接受常見面額。"""
    stock_id = _stock(row)
    effective_date = roc_date(_field(row, "恢復買賣日期"))
    previous_close = amount(_field(row, "停止買賣前收盤價格"))
    reference_price = amount(_field(row, "恢復買賣參考價"))
    keys = _field(row, "詳細資料").replace(" ", "").split(",")
    detail_url = f"{TWSE}/zh/announcement/change/twtb8u-detail.html?{','.join(keys)}"
    action = _new(
        stock_id,
        effective_date,
        PAR_CHANGE,
        previous_close,
        reference_price,
        [list_url, detail_url],
        {"list": row},
    )

    # 參考價 = 前收 ÷ 換股率；官方捨入方式未說明，兩種都接受。
    matches = []
    for par in PAR_VALUES:
        ratio = Decimal(10) / Decimal(par)
        computed = previous_close / ratio
        if reference_price in (
            computed.quantize(TOLERANCE, ROUND_DOWN),
            computed.quantize(TOLERANCE, ROUND_HALF_UP),
        ):
            matches.append(ratio)
    if len(matches) == 1:
        action["shares_per_1000"] = (matches[0] * THOUSAND).normalize()
    else:
        _review(
            action,
            f"無法由前收 {previous_close} 與參考價 {reference_price} 確定換股率（候選 {matches}）",
        )
    return action


def par_change_rows(payload, stocks):
    wanted = set(stocks)
    return [row for row in records(payload) if _stock(row) in wanted]


# ---------- 與已確認停牌的一致性 ----------


def check_halts(actions, confirmed_halts):
    """減資的停止買賣首日應列在已確認停牌清單；沒有時列入人工確認，不自行補登。"""
    for action in actions:
        start = action.get("halt_start")
        if (
            action["kind"] == CAPITAL_REDUCTION
            and start
            and (action["stock_id"], start) not in confirmed_halts
        ):
            _review(action, f"停止買賣首日 {start} 不在已確認停牌清單中")
    return actions


# ---------- 儲存 ----------


def _encode(action, fetched_at):
    row = []
    for column in COLUMNS:
        value = fetched_at if column == "fetched_at" else action[column]
        if column in DECIMAL_COLUMNS:
            value = None if value is None else str(value)
        elif column == "needs_review":
            value = int(bool(value))
        elif column in ("review_notes", "sources", "raw"):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        row.append(value)
    return row


def save_actions(actions, db_path, *, fetched_at):
    """寫入或更新公司行動（主鍵：股票、生效日、類型）；寫入前先升級 schema。"""
    for action in actions:
        if action["kind"] not in KINDS:
            raise ValueError(f"未知的公司行動類型：{action['kind']}")
        if not all(url.startswith("https://") for url in action["sources"]):
            raise ValueError("公司行動來源必須是 https 網址")
    migrate_database(db_path)
    placeholders = ", ".join("?" for _ in COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS[3:])
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.executemany(
            f"INSERT INTO corporate_actions ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(stock_id, effective_date, kind) DO UPDATE SET {updates}",
            [_encode(action, fetched_at) for action in actions],
        )
    return len(actions)


def load_actions(db_path, stock_id=None, start=None, end=None):
    """依生效日排序讀出公司行動；資料庫或表不存在時回傳空清單，不建立檔案。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'corporate_actions'"
        ).fetchone():
            return []
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT {', '.join(COLUMNS)} FROM corporate_actions "
            "WHERE (:stock_id IS NULL OR stock_id = :stock_id) "
            "AND (:start IS NULL OR effective_date >= :start) "
            "AND (:end IS NULL OR effective_date <= :end) "
            "ORDER BY stock_id, effective_date, kind",
            {"stock_id": stock_id, "start": start, "end": end},
        ).fetchall()
    result = []
    for row in rows:
        action = dict(row)
        for column in DECIMAL_COLUMNS:
            action[column] = None if action[column] is None else Decimal(action[column])
        action["needs_review"] = bool(action["needs_review"])
        for column in ("review_notes", "sources", "raw"):
            action[column] = json.loads(action[column])
        result.append(action)
    return result


def summary(db_path):
    """inspect_db 用的摘要：筆數、最新生效日、待人工確認筆數；沒有資料時為 None。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'corporate_actions'"
        ).fetchone():
            return None
        count, latest, review = connection.execute(
            "SELECT COUNT(*), MAX(effective_date), COALESCE(SUM(needs_review), 0) FROM corporate_actions"
        ).fetchone()
    return {"count": count, "latest": latest, "needs_review": review} if count else None
