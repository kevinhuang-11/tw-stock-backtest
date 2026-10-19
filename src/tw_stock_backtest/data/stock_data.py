import json
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def parse_twse_date(value):
    """將民國日期，例如 114/01/02，轉成 2025-01-02。"""
    roc_year, month, day = value.strip().split("/")

    result = date(
        int(roc_year) + 1911,
        int(month),
        int(day),
    )

    return result.isoformat()


def parse_price(value):
    """將價格字串轉為 Decimal；無成交價格回傳 None。"""
    text = value.strip()

    if text == "--":
        return None

    try:
        price = Decimal(text.replace(",", ""))
    except InvalidOperation as error:
        raise ValueError(f"無效的價格：{value}") from error

    if not price.is_finite() or price < 0:
        raise ValueError(f"價格必須是有限且非負的數值：{value}")

    return price


def parse_nonnegative_integer(value):
    """將含逗號的整數字串轉成非負整數。"""
    text = value.strip().replace(",", "")
    number = int(text)

    if number < 0:
        raise ValueError(f"數值不能是負數：{value}")

    return number


def parse_volume(value):
    """將成交股數字串轉為非負整數。"""
    return parse_nonnegative_integer(value)


def normalize_row(stock_id, fields, row):
    """依欄位名稱，將證交所的一列行情轉成統一格式。"""
    if len(fields) != len(row):
        raise ValueError("欄位數量與資料數量不一致")

    raw = dict(zip(fields, row, strict=True))

    required_fields = (
        "日期",
        "成交股數",
        "成交金額",
        "成交筆數",
        "開盤價",
        "最高價",
        "最低價",
        "收盤價",
    )

    for field in required_fields:
        if field not in raw:
            raise ValueError(f"缺少必要欄位：{field}")

    record = {
        "stock_id": stock_id,
        "date": parse_twse_date(raw["日期"]),
        "open": parse_price(raw["開盤價"]),
        "high": parse_price(raw["最高價"]),
        "low": parse_price(raw["最低價"]),
        "close": parse_price(raw["收盤價"]),
        "volume": parse_volume(raw["成交股數"]),
        "turnover": parse_nonnegative_integer(raw["成交金額"]),
        "trade_count": parse_nonnegative_integer(raw["成交筆數"]),
    }

    # 有完整價格時，檢查高低價關係是否合理
    prices = (
        record["open"],
        record["high"],
        record["low"],
        record["close"],
    )

    if all(price is not None for price in prices):
        if not (
            record["low"] <= record["open"] <= record["high"]
            and record["low"] <= record["close"] <= record["high"]
        ):
            raise ValueError(f"{record['date']} 的高低價關係不合理")

    return record


# ---------- 證交所月行情下載 ----------


class NoMarketData(ValueError):
    """來源明確表示無資料；仍相容既有 ValueError 處理。"""


def fetch_month(stock_id, month, *, timeout_seconds):
    """下載指定股票、指定月份的原始行情。"""
    params = {
        "stockNo": stock_id,
        "date": month,
        "response": "json",
    }

    query = urlencode(params)
    url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?{query}"

    request = Request(
        url,
        headers={"User-Agent": "tw-stock-backtest/0.1"},
    )

    with urlopen(request, timeout=timeout_seconds) as response:
        result = json.load(response)

    if not isinstance(result, dict):
        raise ValueError("行情回應格式錯誤，預期為 JSON 物件")

    if result.get("stat") in ("查無資料", "很抱歉，沒有符合條件的資料!"):
        raise NoMarketData("行情查詢未成功：來源明確回覆無資料")

    if result.get("stat") != "OK":
        raise ValueError(f"行情查詢未成功：{result.get('stat')}")

    if not result.get("fields") or not result.get("data"):
        raise ValueError("回應缺少欄位或行情資料")

    return result


def fetch_month_with_retry(
    stock_id,
    month,
    *,
    timeout_seconds,
    max_attempts,
    retry_wait_seconds,
):
    """遇到指定的暫時性錯誤時，有限次數重試。"""
    for name, value in (
        ("max_attempts", max_attempts),
        ("retry_wait_seconds", retry_wait_seconds),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必須是正整數")

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_month(
                stock_id,
                month,
                timeout_seconds=timeout_seconds,
            )

        except HTTPError as error:
            # HTTPError 是 URLError 的子類別，必須先處理。
            if error.code not in (502, 503, 504) or attempt == max_attempts:
                raise

            message = f"HTTP {error.code}"

        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == max_attempts:
                raise

            message = str(error)

        wait_seconds = retry_wait_seconds * (2 ** (attempt - 1))

        print(
            f"查詢失敗：{message}。"
            f"等待 {wait_seconds} 秒後進行第"
            f" {attempt + 1}/{max_attempts} 次嘗試。",
            flush=True,
        )

        time.sleep(wait_seconds)
