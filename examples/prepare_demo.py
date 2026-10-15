"""Create an isolated, synthetic offline demo using existing application APIs.

No network, no user DB/config/notes read, no overwrite. Not market evidence.
"""

import argparse
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from tw_stock_backtest.cli.run_research import main as run_research
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data.supplemental import Store, growth, timestamp
from tw_stock_backtest.data.universe import save_synthetic_universe
from tw_stock_backtest.stock_research import Watchlist
from tw_stock_backtest.strategies import StrategyLibrary, from_settings
from tw_stock_backtest.watchlist_research import observations, save_snapshot
from tw_stock_backtest.workbench import Workbench

# 名稱與產業都明確標示為示範；9001～9003 不在上市普通股名單內。
DEMO_NAMES = {
    "9001": ("示範股 A（合成）", "示範產業甲"),
    "9002": ("示範股 B（合成）", "示範產業乙"),
    "9003": ("示範股 C（合成）", "示範產業丙"),
}


def prepare(output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)  # Refuse even an existing empty directory.
    (root / "config.toml").write_bytes(Path(__file__).with_name("demo.toml").read_bytes())
    (root / "SYNTHETIC.txt").write_text(
        "合成展示資料；非 TWSE 實際行情，不可用於投資或策略績效證明。\n", encoding="utf-8"
    )
    settings = load_config(root / "config.toml")
    # 工作台從行情庫旁的名單檔讀取名稱；合成名單如實標示來源，並使示例環境停用連網更新。
    listed = Path(settings["storage"]["database_path"]).with_name("listed_stocks.json")
    save_synthetic_universe(
        [
            {
                "stock_id": sid,
                "name": DEMO_NAMES[sid][0],
                "industry": DEMO_NAMES[sid][1],
                "market": "合成示例",
            }
            for sid in settings["universe"]["stocks"]
        ],
        listed,
        source_url="synthetic://offline-example",
    )
    days = []
    day = date(2026, 7, 1)
    while day <= date(2026, 9, 29):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    stamp = timestamp()
    store = Store(root / "data/supplemental.sqlite3")
    for offset, sid in enumerate(settings["universe"]["stocks"]):
        rows = []
        for index, day in enumerate(days):
            close = (
                Decimal(80 + offset * 40) + Decimal(index) / 5 + Decimal((index + offset) % 7) / 10
            )
            rows.append(
                {
                    "stock_id": sid,
                    "date": day,
                    "open": close - Decimal(".1"),
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 100000 + index * 100,
                    "turnover": 10000000,
                    "trade_count": 100,
                }
            )
        save_records(rows, db_path=settings["storage"]["database_path"])
        revenues = []
        values = {}
        for n in range(13):
            y, m = divmod(2025 * 12 + 8 + n, 12)
            month = f"{y:04d}-{m + 1:02d}"
            value = str(100000 + offset * 20000 + n * 1000)
            values[month] = value
            d = date(y, m + 1, 1)
            prior = (d - timedelta(days=1)).isoformat()[:7]
            year = f"{y - 1:04d}-{m + 1:02d}"
            revenues.append(
                {
                    "stock_id": sid,
                    "period": month,
                    "revenue": value,
                    "previous_month": values.get(prior),
                    "previous_year": values.get(year),
                    "mom": growth(value, values.get(prior)),
                    "yoy": growth(value, values.get(year)),
                    "official_mom_percent": None,
                    "official_yoy_percent": None,
                    "source_generated_date": None,
                    "source": "synthetic://offline-example",
                    "basis": "合成示例；非官方營收",
                    "unit": "TWD thousand",
                    "published_at": None,
                    "fetched_at": stamp,
                    "raw": {"synthetic": True, "value": value},
                }
            )
        store.save("revenue_history", revenues)
        flows = []
        for day in days[-17:]:
            groups = {
                key: {"buy": "1000", "sell": "1200", "net": "-200"}
                for key in ("foreign", "trust", "foreign_dealer", "dealer_own", "dealer_hedge")
            }
            groups["dealer"] = {"buy": "2000", "sell": "2400", "net": "-400"}
            flows.append(
                {
                    "stock_id": sid,
                    "period": day,
                    "groups": groups,
                    "total": "-800",
                    "unit": "shares",
                    "source": "synthetic://offline-example",
                    "published_at": None,
                    "fetched_at": stamp,
                    "raw": {"synthetic": True, "day": day},
                    "notes": ["合成示例，不是官方交易日曆"],
                }
            )
        store.save("institution", flows)
    library = StrategyLibrary(root / "strategies")
    strategy = from_settings(settings, name="離線合成示例（非真實行情）")
    library.save(strategy)
    state = root / "workbench"
    notes = Watchlist(state / "private/watchlist.sqlite3")
    for sid in settings["universe"]["stocks"]:
        notes.save(sid, "", 0)
    code = run_research(
        [
            "--config",
            str(root / "config.toml"),
            "--skip-download",
            "--as-of",
            "2026-09-29",
            "--universe",
            str(listed),
            "--strategy",
            str(library.path("baseline")),
            "--output-dir",
            str(root / "reports"),
        ]
    )
    if code != 0:
        raise RuntimeError("合成示例研究未完整成功，請查看輸出；原資料未受影響")
    wb = Workbench(root / "config.toml", root / "reports", state, strategy_root=root / "strategies")
    try:
        saved = save_snapshot(
            observations(wb, settings["universe"]["stocks"], "baseline"),
            state / "watchlist-reports",
        )
    finally:
        wb.close()
    print("已建立合成展示；不代表真實市場結果。")
    print("目錄：", output)
    print("初次摘要：", saved.relative_to(root))
    return root


def serve(root, port):
    """以示例目錄啟動工作台；只監聽本機，Ctrl+C 結束。"""
    from tw_stock_backtest.cli.serve_workbench import main as serve_workbench

    print(f"開啟 http://127.0.0.1:{port}/ （Ctrl+C 結束）")
    serve_workbench(
        [
            "--config",
            str(root / "config.toml"),
            "--reports-dir",
            str(root / "reports"),
            "--state-dir",
            str(root / "workbench"),
            "--strategy-dir",
            str(root / "strategies"),
            "--port",
            str(port),
        ]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="reports/offline-demo",
        help="必須是不存在的新目錄；搭配 --serve 時可沿用既有示例",
    )
    parser.add_argument(
        "--serve", action="store_true", help="建立（或沿用既有）示例後直接啟動工作台"
    )
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    # 只有確認是本程式建立的示例才沿用，避免把其他目錄當成示例。
    if args.serve and (root / "SYNTHETIC.txt").is_file():
        print("沿用既有示例：", args.output)
    else:
        try:
            prepare(args.output)
        except FileExistsError:
            parser.exit(1, "展示目錄已存在且不是示例目錄；不覆寫，請沿用或指定新的 --output。\n")
    if args.serve:
        serve(root, args.port)
