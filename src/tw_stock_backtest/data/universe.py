import re
from datetime import date
from html.parser import HTMLParser
from urllib.request import Request, urlopen
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


TWSE_UNIVERSE_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)


class _TableParser(HTMLParser):
    """取出 HTML 表格中的每列與每格文字。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            text = "".join(self._cell)
            text = " ".join(text.split())
            self._row.append(text)
            self._cell = None

        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None


def parse_listed_stocks(html, *, as_of=None):
    """解析上市普通股名單；as_of 僅用來排除尚未上市的資料。"""
    if not isinstance(html, str) or not html.strip():
        raise ValueError("證券名單內容不可為空")

    if as_of is None:
        as_of = date.today()

    if type(as_of) is not date:
        raise ValueError("as_of 必須是 date")

    parser = _TableParser()
    parser.feed(html)
    parser.close()

    required_fields = (
        "有價證券代號及名稱",
        "上市日",
        "市場別",
        "產業別",
        "CFICode",
    )

    positions = None
    stocks = {}

    for row in parser.rows:
        # 移除欄位名稱中的空白，以免網頁排版影響比對。
        headings = [
            re.sub(r"\s+", "", value)
            for value in row
        ]

        if all(field in headings for field in required_fields):
            positions = {
                field: headings.index(field)
                for field in required_fields
            }
            continue

        # 略過標題、分類名稱等非資料列。
        if positions is None:
            continue

        if len(row) <= max(positions.values()):
            continue

        market = row[positions["市場別"]]
        cfi_code = row[positions["CFICode"]].upper()

        # CFI 的 ES 類別表示普通股。
        # 不以「只有四位數字」作為唯一篩選條件。
        if market != "上市" or not cfi_code.startswith("ES"):
            continue

        code_and_name = row[positions["有價證券代號及名稱"]]
        parts = code_and_name.split(maxsplit=1)

        if len(parts) != 2:
            raise ValueError(f"無法辨識股票代號與名稱：{code_and_name}")

        stock_id, name = parts

        if re.fullmatch(r"[0-9]{4}", stock_id) is None:
            raise ValueError(f"上市普通股代號格式不符：{stock_id}")

        listed_text = row[positions["上市日"]]

        try:
            listed_date = date.fromisoformat(
                listed_text.replace("/", "-")
            )
        except ValueError as error:
            raise ValueError(
                f"{stock_id} 的上市日期無效：{listed_text}"
            ) from error

        if listed_date > as_of:
            continue

        if stock_id in stocks:
            raise ValueError(f"股票代號重複：{stock_id}")

        stocks[stock_id] = {
            "stock_id": stock_id,
            "name": name,
            "market": market,
            "industry": row[positions["產業別"]],
            "listed_date": listed_date.isoformat(),
            "cfi_code": cfi_code,
        }

    if positions is None:
        raise ValueError("找不到證券名單欄位，來源網頁可能已變更")

    if not stocks:
        raise ValueError("來源未提供符合條件的上市普通股")

    return [stocks[stock_id] for stock_id in sorted(stocks)]


def fetch_listed_stocks(*, timeout=30):
    """下載證交所目前的上市證券名單。"""
    request = Request(
        TWSE_UNIVERSE_URL,
        headers={"User-Agent": "tw-stock-backtest/0.1"},
    )

    with urlopen(request, timeout=timeout) as response:
        raw = response.read()

    # 支援 UTF-8 與台灣網頁常見的 Big5／CP950 編碼。
    try:
        html = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        html = raw.decode("cp950")

    return parse_listed_stocks(html)

def _validate_stock_list(stocks):
    """檢查要保存或讀取的股票名單。"""
    if not isinstance(stocks, list) or not stocks:
        raise ValueError("股票名單必須是非空清單")

    required_fields = (
        "stock_id",
        "name",
        "market",
        "industry",
        "listed_date",
        "cfi_code",
    )

    seen = set()

    for stock in stocks:
        if not isinstance(stock, dict):
            raise ValueError("每筆股票資料必須是字典")

        for field in required_fields:
            if not isinstance(stock.get(field), str):
                raise ValueError(f"股票資料缺少或無效的欄位：{field}")

        stock_id = stock["stock_id"]

        if re.fullmatch(r"[0-9]{4}", stock_id) is None:
            raise ValueError(f"股票代號格式不符：{stock_id}")

        if stock_id in seen:
            raise ValueError(f"股票代號重複：{stock_id}")

        seen.add(stock_id)

        if not stock["name"].strip():
            raise ValueError(f"{stock_id} 的股票名稱不可為空")

        if (
            stock["market"] != "上市"
            or not stock["cfi_code"].startswith("ES")
        ):
            raise ValueError(f"{stock_id} 不符合上市普通股條件")

        date.fromisoformat(stock["listed_date"])


def save_universe(stocks, path):
    """保存名單；完整寫入暫存檔後，才替換目標檔案。"""
    _validate_stock_list(stocks)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "schema_version": 1,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "source_url": TWSE_UNIVERSE_URL,
        "scope": "current_twse_common_stocks",
        "count": len(stocks),
        "stocks": sorted(stocks, key=lambda stock: stock["stock_id"]),
    }

    temporary_path = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)

            json.dump(
                snapshot,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return snapshot


def load_universe(path):
    """讀取並驗證保存的名單，不會連線更新。"""
    with Path(path).open(encoding="utf-8") as file:
        snapshot = json.load(file)

    if not isinstance(snapshot, dict):
        raise ValueError("名單檔案格式錯誤")

    if (
        type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != 1
    ):
        raise ValueError("不支援的名單格式版本")

    if snapshot.get("scope") != "current_twse_common_stocks":
        raise ValueError("名單範圍不符合目前上市普通股")

    if snapshot.get("source_url") != TWSE_UNIVERSE_URL:
        raise ValueError("名單來源不符")

    downloaded_at = snapshot.get("downloaded_at")

    if not isinstance(downloaded_at, str):
        raise ValueError("名單缺少下載時間")

    timestamp = datetime.fromisoformat(downloaded_at)

    if timestamp.utcoffset() is None:
        raise ValueError("下載時間必須包含時區")

    stocks = snapshot.get("stocks")
    _validate_stock_list(stocks)

    if (
        type(snapshot.get("count")) is not int
        or snapshot["count"] != len(stocks)
    ):
        raise ValueError("名單筆數與實際內容不符")

    return snapshot