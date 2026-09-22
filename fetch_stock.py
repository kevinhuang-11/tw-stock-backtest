import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from stock_data import normalize_row


def fetch_month(stock_id, month):
    # 查詢參數：股票代號、月份，以及回傳格式
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

    # 最多等待 30 秒，取得回應後轉成 Python 資料
    with urlopen(request, timeout=30) as response:
        result = json.load(response)

    if result.get("stat") != "OK":
        raise ValueError(f"行情查詢未成功：{result.get('stat')}")

    if not result.get("fields") or not result.get("data"):
        raise ValueError("回應缺少欄位或行情資料")

    return result


def main():
    stock_id = "2330"

    try:
        result = fetch_month(stock_id, "20260901")

        records = [
            normalize_row(stock_id, result["fields"], row)
            for row in result["data"]
        ]
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        print(f"下載或整理資料失敗：{error}")
        return

    print("資料標題：", result.get("title"))
    print("整理後筆數：", len(records))

    print("\n前 16 筆整理後資料：")
    for record in records[:16]:
        print(record)


if __name__ == "__main__":
    main()