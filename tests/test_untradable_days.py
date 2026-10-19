"""個股頁的不可交易日（docs/HALT-POLICY.md G11）：停牌、無成交價、缺列與終止上市的區段。"""

import re
import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG
from test_strategy_library import records

from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.data.market_events import untradable_ranges
from tw_stock_backtest.workbench import Workbench, create_app

DAYS = [(date(2026, 8, 3) + timedelta(days=i)).isoformat() for i in range(12)]
HALT = {"reason": "減資換發新股", "source": "https://x", "sources": ["https://x"]}


def row(day, price=D("10")):
    return {
        "stock_id": "AAA",
        "date": day,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1,
        "turnover": 1,
        "trade_count": 1,
    }


class RangeTests(unittest.TestCase):
    def test_each_kind_is_grouped_into_ranges(self):
        rows = [row(day) for day in DAYS[:8]]
        rows[2] = row(DAYS[2], None)  # 無成交價
        for i in (4, 5):
            rows[i] = {**row(DAYS[i], None), "volume": 0, "turnover": 0, "trade_count": 0}
        del rows[7]  # 缺列
        halts = {("AAA", DAYS[i]): HALT for i in (4, 5)}
        delistings = {"AAA": {"effective": DAYS[9], "reason": "測試終止上市"}}
        ranges = untradable_ranges(
            "AAA", rows, DAYS[:11], confirmed_halts=halts, delistings=delistings
        )
        self.assertEqual(
            [(r["start"], r["end"], r["days"], r["status"]) for r in ranges],
            [
                (DAYS[2], DAYS[2], 1, "NO_PRICE"),
                (DAYS[4], DAYS[5], 2, "CONFIRMED_HALT"),
                (DAYS[7], DAYS[8], 2, "MISSING_ROW"),
                (DAYS[9], DAYS[10], 2, "DELISTED"),
            ],
        )
        self.assertEqual(ranges[1]["reason"], "減資換發新股")

    def test_non_adjacent_days_are_separate_ranges(self):
        rows = [row(day) for day in DAYS[:6]]
        del rows[4]
        del rows[1]
        ranges = untradable_ranges("AAA", rows, DAYS[:6], confirmed_halts={}, delistings={})
        self.assertEqual(
            [(r["start"], r["end"]) for r in ranges], [(DAYS[1], DAYS[1]), (DAYS[4], DAYS[4])]
        )


class StockPageTests(unittest.TestCase):
    def test_stock_page_lists_untradable_days(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.toml"
            config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
            wb = Workbench(config, root / "reports", root / "state")
            self.addCleanup(wb.close)
            rows = records(count=40) + records("2317", count=40)
            gap = rows[30]["date"]
            del rows[30]  # 2330 在其他股票有行情的一天沒有列
            save_records(rows, db_path=wb.settings["storage"]["database_path"])
            client = create_app(wb).test_client()
            text = client.get(f"/stocks/2330?as_of={rows[38]['date']}").text
            self.assertIn("<strong>不可交易的日子</strong>", text)
            self.assertIn(f"{gap}（1 個行情日）", text)
            self.assertIn("缺少行情列（原因未確認）", text)
            # 分析日以後的缺列不列出（只看到分析日）。
            with patch("tw_stock_backtest.stock_research.CONFIRMED_HALTS", {}):
                early = client.get(f"/stocks/2330?as_of={rows[20]['date']}").text
            self.assertNotIn("<strong>不可交易的日子</strong>", early)
            self.assertTrue(re.search("走勢與指標", early))


if __name__ == "__main__":
    unittest.main()
