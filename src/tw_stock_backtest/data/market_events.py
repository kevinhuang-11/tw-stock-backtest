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
MISSING_ROW_EVENT = {
    "reason": "缺少行情列（原因未確認，不認定為停牌）",
    "source": "其他股票當天有行情，這檔沒有列；查不到停止買賣公告",
}

# 整批保護（docs/HALT-POLICY.md 3.7）：某個行情日缺列的股票超過股票池的這個比例、
# 而且至少 MISSING_BATCH_MINIMUM 檔時，判定為行情未更新完整，整次失敗。
MISSING_BATCH_RATIO = Decimal("0.1")
MISSING_BATCH_MINIMUM = 2


def market_event(record, confirmed_halts):
    """不可交易日的說明：已確認停牌附上來源；無成交價與缺列說明原因未確認。"""
    if record["market_status"] == "CONFIRMED_HALT":
        return {
            **confirmed_halts[(record["stock_id"], record["date"])],
            "market_status": "CONFIRMED_HALT",
        }
    if record["market_status"] == "MISSING_ROW":
        return {**MISSING_ROW_EVENT, "market_status": "MISSING_ROW"}
    return {**NO_PRICE_EVENT, "market_status": "NO_PRICE"}


def not_tradable_reason(record):
    if record["market_status"] == "CONFIRMED_HALT":
        return "已確認停牌"
    if record["market_status"] == "MISSING_ROW":
        return "缺少行情列（原因未確認）"
    return "無成交價（原因未確認）"


def missing_row(stock_id, day):
    """缺列在記憶體中的代表：沒有價格、量為 0，並標記 missing_row。不寫入行情庫。"""
    return {
        "stock_id": stock_id,
        "date": day,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": 0,
        "turnover": 0,
        "trade_count": 0,
        "missing_row": True,
    }


def prepare_market_records(
    records,
    *,
    confirmed_halts,
    allow_unpriced_halt=False,
):
    """保留原始價格，增加交易狀態與估值價格。

    不可交易日有兩種：列在 confirmed_halts 的停牌，以及來源有該日但開高低收全為空的
    無成交價日。兩者都不補價格；估值沿用最近一筆有效收盤並標示其日期。
    標記 missing_row 的列（prepare_backtest_market 為缺列補的代表）狀態為 MISSING_ROW。
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
        missing = no_price and record.get("missing_row") is True

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
                    "CONFIRMED_HALT"
                    if is_halted
                    else "MISSING_ROW"
                    if missing
                    else "NO_PRICE"
                    if no_price
                    else "NORMAL"
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
    """整理各股票行情，保留暖機資料與共同回測日期。

    行情日是期間內任何一檔有列的日期。期間內某檔缺列、又不在停牌清單時，補上一列
    MISSING_ROW（原因未確認，不可交易，估值沿用最近收盤；見 docs/HALT-POLICY.md 3.6），
    只存在記憶體中。暖機期間的缺列不補，與研究排名讀到的序列相同。

    兩種情況仍整次失敗（3.7、3.8）：股票在 end 以前沒有任何列（尚未下載），以及某個
    行情日缺列的股票太多（行情未更新完整）。
    """
    if not records_by_stock:
        raise ValueError("股票池不可為空")

    trading_dates = set()
    never = []

    for stock_id, records in records_by_stock.items():
        if any(record["stock_id"] != stock_id for record in records):
            raise ValueError(f"{stock_id} 的行情混入其他股票")

        dates = [record["date"] for record in records if date.fromisoformat(record["date"]) <= end]
        if not dates:
            never.append(stock_id)
        trading_dates.update(day for day in dates if start <= date.fromisoformat(day) <= end)

    if never:
        raise ValueError(
            f"{', '.join(sorted(never))} 在 {end} 以前沒有任何行情列，可能尚未下載；"
            "請先下載行情，或將這些股票移出股票池"
        )

    if not trading_dates:
        raise ValueError("指定期間沒有可回測的行情")

    present = {
        stock_id: {record["date"] for record in records}
        for stock_id, records in records_by_stock.items()
    }
    missing = {
        day: sorted(stock_id for stock_id, days in present.items() if day not in days)
        for day in sorted(trading_dates)
    }
    threshold = MISSING_BATCH_RATIO * len(records_by_stock)
    for day, stocks in missing.items():
        if len(stocks) >= MISSING_BATCH_MINIMUM and len(stocks) > threshold:
            complete = [d for d, s in missing.items() if d < day and not s]
            hint = f"；最後一個資料完整的行情日為 {complete[-1]}" if complete else ""
            raise ValueError(
                f"{day} 有 {len(stocks)} 檔（共 {len(records_by_stock)} 檔）缺少行情列，"
                f"例如 {', '.join(stocks[:5])}；行情可能未更新完整，不當成個別股票的缺漏。"
                f"請更新行情，或把結束日提前{hint}"
            )

    market = {}
    for stock_id, records in records_by_stock.items():
        filled = [record for record in records if date.fromisoformat(record["date"]) <= end]
        filled += [
            missing_row(stock_id, day) for day, stocks in missing.items() if stock_id in stocks
        ]
        filled.sort(key=lambda record: record["date"])
        prepared = prepare_market_records(
            filled,
            confirmed_halts=confirmed_halts,
            allow_unpriced_halt=True,
        )
        market[stock_id] = {record["date"]: record for record in prepared}

    return market, sorted(trading_dates)


def unconfirmed_gaps(market):
    """回測期間內的 MISSING_ROW，依股票整理成 [{stock_id, first, last, days}]。"""
    result = []
    for stock_id, prices in sorted(market.items()):
        days = sorted(d for d, r in prices.items() if r["market_status"] == "MISSING_ROW")
        if days:
            result.append(
                {"stock_id": stock_id, "first": days[0], "last": days[-1], "days": len(days)}
            )
    return result
