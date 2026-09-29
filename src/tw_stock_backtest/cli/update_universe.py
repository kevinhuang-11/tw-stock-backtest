import argparse
from pathlib import Path
from urllib.error import URLError

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH
from tw_stock_backtest.data.universe import (
    fetch_listed_stocks,
    save_universe,
)


DEFAULT_UNIVERSE_PATH = (
    DEFAULT_CONFIG_PATH.parent / "data" / "listed_stocks.json"
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="下載並保存目前上市普通股名單"
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_UNIVERSE_PATH,
        help="名單 JSON 儲存位置",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()
    output_path = args.output.resolve()

    try:
        stocks = fetch_listed_stocks()
        snapshot = save_universe(stocks, output_path)

    except (ValueError, OSError, URLError) as error:
        print(f"更新股票名單失敗：{error}")
        return 1

    print("名單範圍：目前上市普通股")
    print(f"股票數量：{snapshot['count']}")
    print(f"下載時間（UTC）：{snapshot['downloaded_at']}")
    print(f"來源：{snapshot['source_url']}")
    print(f"儲存位置：{output_path}")
    print("此名單用於目前市場篩選，不代表歷史完整股票池。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())