"""報表與說明（docs/HALT-POLICY.md 3.12～3.14、3.16、D8）：研究的排除原因、期末持股與基準未買進。

全部使用合成資料。
"""

import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_missing_prices import series, trading_days
from test_ui_pages import PageCase, write

from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.cli.run_portfolio import position_text
from tw_stock_backtest.cli.screen_factors import collect_factor_rows
from tw_stock_backtest.data.database import save_records

DAYS = trading_days(60)
COSTS = CostSettings(
    commission_rate=Decimal("0"),
    commission_discount=Decimal("1"),
    minimum_commission=Decimal("0"),
    sell_tax_rate=Decimal("0"),
)
EVENT = {"reason": "減資換發新股", "source": "https://x", "sources": ["https://x"]}


class ResearchReasonTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "stocks.db"
        rows = series("AAA", 60) + series("BBB", 60)
        # AAA 的最後一天沒有價格（來源有列）。
        rows[59] = {**rows[59], "open": None, "high": None, "low": None, "close": None}
        save_records(rows, db_path=self.db)

    def reason(self, stock_id, as_of, halts=None, delistings=None):
        with (
            patch("tw_stock_backtest.data.database.CONFIRMED_HALTS", halts or {}),
            patch("tw_stock_backtest.cli.screen_factors.CONFIRMED_HALTS", halts or {}),
            patch("tw_stock_backtest.cli.screen_factors.CONFIRMED_DELISTINGS", delistings or {}),
        ):
            rows, errors = collect_factor_rows(
                [{"stock_id": stock_id}], as_of, db_path=self.db, factor_settings={}
            )
        self.assertEqual(rows, [])
        return errors[0]["reason"]

    def test_no_price_on_the_analysis_date(self):
        self.assertEqual(self.reason("AAA", DAYS[59]), "分析日無成交價（原因未確認，不認定為停牌）")

    def test_confirmed_halt_on_the_analysis_date(self):
        # 停牌日來源沒有列，load_records 補上停牌列；原因直接寫出停牌。
        with TemporaryDirectory() as folder:
            db = Path(folder) / "stocks.db"
            save_records(series("AAA", 59) + series("BBB", 60), db_path=db)
            self.db = db
            reason = self.reason("AAA", DAYS[59], halts={("AAA", DAYS[59]): EVENT})
        self.assertEqual(reason, "分析日已確認停牌：減資換發新股")

    def test_delisted_stock(self):
        delisting = {"AAA": {"effective": DAYS[58], "reason": "測試終止上市"}}
        with TemporaryDirectory() as folder:
            db = Path(folder) / "stocks.db"
            save_records(series("AAA", 58) + series("BBB", 60), db_path=db)
            self.db = db
            reason = self.reason("AAA", DAYS[59], delistings=delisting)
        self.assertEqual(
            reason, f"已於 {DAYS[58]} 終止上市（測試終止上市）；最後行情日期為 {DAYS[57]}"
        )


class FinalPositionTests(unittest.TestCase):
    def backtest(self, records, *, halts=None, delistings=None):
        return run_buy_and_hold(
            records,
            DAYS[40],
            DAYS[-1],
            initial_cash=Decimal("300000"),
            cost_settings=COSTS,
            confirmed_halts=halts or {},
            delistings=delistings or {},
        )

    def test_halted_position_at_the_end_is_labelled_with_its_price_date(self):
        records = {sid: series(sid, 60) for sid in ("AAA", "BBB")}
        halts = {("AAA", day): EVENT for day in DAYS[57:]}
        for index in (57, 58, 59):
            records["AAA"][index] = {
                **records["AAA"][index],
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": 0,
                "turnover": 0,
                "trade_count": 0,
            }
        result = self.backtest(records, halts=halts)
        aaa = next(p for p in result["final_positions"] if p["stock_id"] == "AAA")
        self.assertEqual((aaa["status"], aaa["status_label"]), ("CONFIRMED_HALT", "停牌中"))
        self.assertEqual(aaa["price"], records["AAA"][56]["close"])
        self.assertEqual(aaa["price_date"], DAYS[56])
        self.assertEqual(aaa["value"], aaa["price"] * aaa["shares"])
        self.assertIn(f"停牌中，以 {DAYS[56]} 收盤", position_text(aaa))
        bbb = next(p for p in result["final_positions"] if p["stock_id"] == "BBB")
        self.assertEqual(position_text(bbb), f"BBB：{bbb['shares']} 股")

    def test_announced_delisting_is_shown_only_after_the_announcement(self):
        records = {sid: series(sid, 60) for sid in ("AAA", "BBB")}

        def notice(announced):
            return {
                "AAA": {
                    "stock_id": "AAA",
                    "announced": announced,
                    "effective": "2026-12-31",
                    "kind": "financial",
                    "reason": "測試",
                    "consideration": None,
                    "sources": ["https://x"],
                }
            }

        known = self.backtest(records, delistings=notice(DAYS[50]))
        aaa = next(p for p in known["final_positions"] if p["stock_id"] == "AAA")
        self.assertEqual(aaa["delisting_announced"]["effective"], "2026-12-31")
        self.assertIn("公告 2026-12-31 終止上市", position_text(aaa))
        # 公告日在結束日以後：結束日時還不知道，不標示。
        later = self.backtest(records, delistings=notice("2026-12-01"))
        aaa = next(p for p in later["final_positions"] if p["stock_id"] == "AAA")
        self.assertIsNone(aaa["delisting_announced"])


class ReportPageTests(PageCase):
    def page(self):
        key = next(iter(self.workbench.catalog()))
        return self.client.get(f"/reports/{key}").text

    def test_final_positions_and_benchmark_gaps_are_listed(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        halted = {
            "stock_id": "6550",
            "shares": 1000,
            "price": "9.92",
            "price_date": "2026-09-16",
            "value": "9920",
            "status": "CONFIRMED_HALT",
            "status_label": "停牌中",
            "delisting_announced": None,
        }
        gone = {
            **halted,
            "stock_id": "1589",
            "status": "UNREALIZABLE",
            "status_label": "無法變現（已終止上市）",
            "delisting_announced": {"announced": "2026-10-08", "effective": "2026-11-18"},
        }
        normal = {**halted, "stock_id": "2330", "status": "NORMAL", "status_label": "正常"}
        results["strategy"]["result"] = {"final_positions": [halted, normal]}
        results["benchmark"]["result"] = {
            "final_positions": [normal, gone],
            "unbought": ["3008"],
            "unavailable_entire_period": ["1589"],
        }
        write(folder, "results.json", results)
        text = self.page()
        self.assertIn("<h2>期末持股</h2>", text)
        self.assertIn("停牌中</span>", text)
        self.assertIn("2026-09-16", text)
        self.assertIn("無法變現（已終止上市）</span>", text)
        self.assertIn("2026-10-08 公告、2026-11-18 終止上市", text)
        # 基準只列出不是正常狀態的持股。
        self.assertEqual(text.count('<a href="/stocks/2330">'), 1)
        self.assertIn("<h2>基準未買進的股票</h2>", text)
        self.assertIn("整段期間都無法交易：1589", text)
        self.assertIn("預算不足以買進一股：3008", text)


if __name__ == "__main__":
    unittest.main()
