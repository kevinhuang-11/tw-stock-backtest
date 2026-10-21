import json
import re
from datetime import date, timedelta
from decimal import Decimal
from importlib import resources

MAX_HALT_DAYS = 400


def load_confirmed_halts(path=None):
    """讀取已確認停牌清單，展開成 {(股票, 日期): {"reason", "source", "sources"}}。

    資料檔（預設為套件內的 confirmed_halts.json）每筆是一段含頭尾的日曆日區間，
    必須附上至少一個 https 來源。區間內的週末或休市日也會展開，但只有實際出現的
    行情日會被查到，不影響結果。格式不符時直接失敗，不略過任何一筆。
    """
    if path is None:
        text = (
            resources.files(__package__)
            .joinpath("confirmed_halts.json")
            .read_text(encoding="utf-8")
        )
    else:
        text = open(path, encoding="utf-8").read()

    document = json.loads(text)
    if document.get("schema_version") != 1 or not isinstance(document.get("halts"), list):
        raise ValueError("已確認停牌清單格式或 schema_version 無效")

    halts = {}
    for entry in document["halts"]:
        stock_id = entry.get("stock_id")
        reason = entry.get("reason")
        sources = entry.get("sources")
        if not isinstance(stock_id, str) or not re.fullmatch(r"\d{4}", stock_id):
            raise ValueError(f"已確認停牌的股票代號無效：{stock_id!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{stock_id} 的停牌原因不可為空")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(isinstance(url, str) and url.startswith("https://") for url in sources)
        ):
            raise ValueError(f"{stock_id} 的停牌必須附上 https 來源")

        start = date.fromisoformat(entry.get("start", ""))
        end = date.fromisoformat(entry.get("end", ""))
        if not start <= end or (end - start).days >= MAX_HALT_DAYS:
            raise ValueError(f"{stock_id} 的停牌區間無效：{start} ～ {end}")

        event = {"reason": reason, "source": sources[0], "sources": list(sources)}
        current = start
        while current <= end:
            key = (stock_id, current.isoformat())
            if key in halts:
                raise ValueError(f"{stock_id} 的停牌區間重疊：{key[1]}")
            halts[key] = event
            current += timedelta(days=1)

    return halts


# 未列入清單的缺價紀錄不認定為停牌，只標示為無成交價（原因未確認）。
CONFIRMED_HALTS = load_confirmed_halts()


PRICE_FIELDS = ("open", "high", "low", "close")
NO_PRICE_EVENT = {
    "reason": "無成交價（原因未確認，不認定為停牌）",
    "source": "行情來源當日有這一列，但沒有開高低收",
}


def market_event(record, confirmed_halts):
    """不可交易日的說明：已確認停牌附上來源，無成交價則說明原因未確認。"""
    if record["market_status"] == "CONFIRMED_HALT":
        return {
            **confirmed_halts[(record["stock_id"], record["date"])],
            "market_status": "CONFIRMED_HALT",
        }
    return {**NO_PRICE_EVENT, "market_status": "NO_PRICE"}


def not_tradable_reason(record):
    if record["market_status"] == "CONFIRMED_HALT":
        return "已確認停牌"
    return "無成交價（原因未確認）"


def prepare_market_records(
    records,
    *,
    confirmed_halts,
    allow_unpriced_halt=False,
):
    """保留原始價格，增加交易狀態與估值價格。

    不可交易日有兩種：列在 confirmed_halts 的停牌，以及來源有該日但開高低收全為空的
    無成交價日。兩者都不補價格；估值沿用最近一筆有效收盤並標示其日期。
    """
    prepared = []
    stock_id = None
    previous_date = None
    last_close = None
    last_close_date = None

    for record in records:
        current_date = date.fromisoformat(record["date"])

        if stock_id is None:
            stock_id = record["stock_id"]
        elif record["stock_id"] != stock_id:
            raise ValueError("行情不可混合不同股票")

        if previous_date is not None and current_date <= previous_date:
            raise ValueError("行情日期必須遞增且不可重複")

        previous_date = current_date
        row = record.copy()

        for field in ("volume", "turnover", "trade_count"):
            if field in record:
                value = record[field]

                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{field} 必須是非負整數")

        is_halted = (
            stock_id,
            record["date"],
        ) in confirmed_halts

        # 來源有這一天，但四個價格都是空的；只有部分價格為空仍視為資料錯誤。
        no_price = not is_halted and all(
            field in record and record[field] is None for field in PRICE_FIELDS
        )

        if is_halted:
            valid = all(
                field in record and record[field] is None
                for field in ("open", "high", "low", "close")
            ) and all(
                field in record and record[field] == 0
                for field in ("volume", "turnover", "trade_count")
            )

            if not valid:
                raise ValueError("行情與已確認停牌事件不一致")

            if last_close is None and not allow_unpriced_halt:
                raise ValueError("停牌前沒有有效收盤價可供估值")

        elif no_price:
            if last_close is None and not allow_unpriced_halt:
                raise ValueError("無成交價日之前沒有有效收盤價可供估值")

        else:
            if "open" not in record or "close" not in record:
                raise ValueError("正常行情至少需要 open 與 close")

            # 接受既有人工測試只提供 open、close 的格式。
            for field in ("open", "high", "low", "close"):
                if field not in record:
                    continue

                value = record[field]

                if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                    raise ValueError(f"{stock_id} / {record['date']} 有無效價格，且未確認為停牌")

            if ("high" in record) != ("low" in record):
                raise ValueError("high 與 low 必須一起提供")

            if "high" in record and not (
                record["low"] <= record["open"] <= record["high"]
                and record["low"] <= record["close"] <= record["high"]
            ):
                raise ValueError("高低價範圍異常")

            last_close = record["close"]
            last_close_date = record["date"]

        row.update(
            {
                "tradable": not (is_halted or no_price),
                "valuation_close": last_close,
                "valuation_price_date": last_close_date,
                "market_status": (
                    "CONFIRMED_HALT" if is_halted else "NO_PRICE" if no_price else "NORMAL"
                ),
            }
        )

        prepared.append(row)

    return prepared


def prepare_backtest_market(
    records_by_stock,
    start,
    end,
    *,
    confirmed_halts,
):
    """整理各股票行情，保留暖機資料與共同回測日期。"""
    if not records_by_stock:
        raise ValueError("股票池不可為空")

    market = {}
    trading_dates = set()

    for stock_id, records in records_by_stock.items():
        if any(record["stock_id"] != stock_id for record in records):
            raise ValueError(f"{stock_id} 的行情混入其他股票")

        prepared = prepare_market_records(
            [record for record in records if date.fromisoformat(record["date"]) <= end],
            confirmed_halts=confirmed_halts,
            allow_unpriced_halt=True,
        )

        market[stock_id] = {record["date"]: record for record in prepared}

        trading_dates.update(
            record["date"]
            for record in prepared
            if start <= date.fromisoformat(record["date"]) <= end
        )

    if not trading_dates:
        raise ValueError("指定期間沒有可回測的行情")

    for stock_id, prices in market.items():
        # 依日期檢查，訊息回報最早缺的一天。
        for trading_date in sorted(trading_dates):
            if trading_date not in prices:
                raise ValueError(
                    f"{stock_id} 缺少 {trading_date} 的行情列（其他股票當天有行情），"
                    "可能是下載缺漏，不能當作停牌或無成交；"
                    "請重新下載該月行情，或將這檔移出股票池"
                )

    return market, sorted(trading_dates)
