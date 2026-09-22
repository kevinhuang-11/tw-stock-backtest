import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
    try:
        # 2025 年 1 月；日期中的日填 01，查詢整個月份
        result = fetch_month("2330", "20250101")
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        print(f"下載失敗：{error}")
        return

    print("資料標題：", result.get("title"))
    print("欄位名稱：", result["fields"])
    print("資料筆數：", len(result["data"]))

    print("\n前 5 筆資料：")
    for row in result["data"][:5]:
        print(row)


if __name__ == "__main__":
    main()