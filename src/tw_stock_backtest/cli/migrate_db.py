import argparse
import sqlite3

from tw_stock_backtest.config import DEFAULT_CONFIG_PATH, load_config
from tw_stock_backtest.data.database import (
    SCHEMA_VERSION,
    migrate_database,
    read_schema_version,
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="把行情庫升級到目前的 schema 版本（只新增表或索引，不改動行情資料）"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定檔位置，預設使用專案中的 config.toml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只顯示目前版本，不修改資料庫；需要升級時退出碼為 2",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        settings = load_config(args.config)
        db_path = settings["storage"]["database_path"]

        if args.check:
            current = read_schema_version(db_path)

            if current is None:
                print(f"行情庫不存在：{db_path}")
                return 1

            print(f"行情庫：{db_path}")
            print(f"schema 版本：{current}（程式支援 {SCHEMA_VERSION}）")

            if current > SCHEMA_VERSION:
                print("資料庫版本比程式新，請更新程式。")
                return 1

            if current < SCHEMA_VERSION:
                print("需要升級；不加 --check 執行即可。")
                return 2

            print("已是最新版本。")
            return 0

        if not db_path.is_file():
            # 不替不存在的路徑建立空資料庫，避免設定錯誤時默默產生新檔。
            print(f"行情庫不存在：{db_path}")
            return 1

        before, after = migrate_database(db_path)

    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"升級失敗：{error}")
        return 1

    print(f"行情庫：{db_path}")
    print(
        f"schema 版本：{before} → {after}"
        if before != after
        else f"schema 版本：{after}，不需要升級"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
