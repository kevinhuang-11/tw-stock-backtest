"""終止上市（docs/HALT-POLICY.md 3.3、3.10、3.16）：清單驗證、回測中的無法變現與估值設定。

行情為合成資料；套件內的清單只檢查格式與已查證的案例。
"""

import json
import tomllib
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from test_missing_prices import series, trading_days

from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.config import validate_settings
from tw_stock_backtest.data.market_events import (
    CONFIRMED_DELISTINGS,
    load_confirmed_halts,
    load_delistings,
)
from tw_stock_backtest.data.universe import listing_exclusions

SOURCE = "https://www.twse.com.tw/example.pdf"
DAYS = trading_days(60)
GONE = DAYS[50]
COSTS = CostSettings(
    commission_rate=Decimal("0"),
    commission_discount=Decimal("1"),
    minimum_commission=Decimal("0"),
    sell_tax_rate=Decimal("0"),
)
FACTORS = {
    "short_window": 2,
    "long_window": 5,
    "momentum_window": 3,
    "volatility_window": 3,
    "top_n": 3,
    "momentum_weight": Decimal("1"),
    "trend_weight": Decimal("1"),
    "volatility_weight": Decimal("1"),
}


def delisting(stock_id="AAA", effective=GONE, announced=None, kind="financial"):
    return {
        stock_id: {
            "stock_id": stock_id,
            "announced": announced or DAYS[45],
            "effective": effective,
            "kind": kind,
            "reason": "測試終止上市",
            "consideration": None,
            "sources": [SOURCE],
        }
    }


def market(*stocks, cut="AAA"):
    """各檔 60 個行情日；cut 這檔在終止上市日起沒有列。"""
    records = {sid: series(sid, 60) for sid in stocks}
    records[cut] = [r for r in records[cut] if r["date"] < GONE]
    return records


class FileTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "halts.json"

    def load(self, delistings, halts=(), schema_version=2):
        self.path.write_text(
            json.dumps(
                {"schema_version": schema_version, "halts": list(halts), "delistings": delistings}
            ),
            encoding="utf-8",
        )
        return load_delistings(self.path)

    def entry(self, **changes):
        return {
            "stock_id": "1234",
            "announced": "2026-10-08",
            "effective": "2026-11-18",
            "kind": "financial",
            "reason": "測試",
            "sources": [SOURCE],
            **changes,
        }

    def test_packaged_cases_are_verified_with_official_sources(self):
        self.assertEqual(sorted(CONFIRMED_DELISTINGS), ["1589", "2867", "3454", "6423", "6806"])
        self.assertEqual(CONFIRMED_DELISTINGS["1589"]["effective"], "2026-11-18")
        self.assertEqual(CONFIRMED_DELISTINGS["1589"]["announced"], "2026-10-08")
        self.assertEqual(CONFIRMED_DELISTINGS["2867"]["kind"], "share_swap")
        self.assertEqual(CONFIRMED_DELISTINGS["6423"]["kind"], "transfer_to_tpex")
        for item in CONFIRMED_DELISTINGS.values():
            self.assertTrue(
                all(url.startswith("https://www.twse.com.tw/") for url in item["sources"])
            )
        # 股份轉換的停止買賣期間也在停牌清單，並在終止上市前一天結束。
        halts = load_confirmed_halts()
        self.assertIn(("2867", "2026-08-31"), halts)
        self.assertNotIn(("2867", "2026-09-01"), halts)
        self.assertIn(("3454", "2026-03-26"), halts)

    def test_invalid_entries_are_rejected(self):
        cases = {
            "類型": self.entry(kind="bankrupt"),
            "公告晚於生效": self.entry(announced="2026-12-01"),
            "沒有來源": self.entry(sources=[]),
            "非 https": self.entry(sources=["http://x"]),
            "空白原因": self.entry(reason=""),
            "對價不是文字": self.entry(consideration=3),
        }
        for name, bad in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.load([bad])
        with self.assertRaisesRegex(ValueError, "重複"):
            self.load([self.entry(), self.entry()])

    def test_halt_cannot_run_past_the_delisting(self):
        halt = {
            "stock_id": "1234",
            "start": "2026-11-10",
            "end": "2026-11-18",
            "reason": "測試",
            "sources": [SOURCE],
        }
        with self.assertRaisesRegex(ValueError, "延伸到終止上市日以後"):
            self.load([self.entry()], halts=[halt])

    def test_schema_one_has_no_delistings(self):
        self.assertEqual(self.load([self.entry()], schema_version=1), {})

    def test_valuation_setting(self):
        base = tomllib.loads(TEST_CONFIG)
        self.assertEqual(validate_settings(base)["halts"], {"delisted_valuation": "last_close"})
        zero = validate_settings({**base, "halts": {"delisted_valuation": "zero"}})
        self.assertEqual(zero["halts"]["delisted_valuation"], "zero")
        with self.assertRaises(ValueError):
            validate_settings({**base, "halts": {"delisted_valuation": "half"}})


class BacktestTests(unittest.TestCase):
    def run_portfolio(self, records, *, delistings, mode="last_close", end=DAYS[-1], positions=2):
        return run_portfolio_backtest(
            records,
            DAYS[40],
            end,
            screening_settings=None,
            ranking_method="factors",
            factor_settings=FACTORS,
            initial_cash=Decimal("1000000"),
            quantity=100,
            max_positions=positions,
            cost_settings=COSTS,
            confirmed_halts={},
            delistings=delistings,
            delisted_valuation=mode,
        )

    def run_benchmark(self, records, *, delistings, mode="last_close"):
        return run_buy_and_hold(
            records,
            DAYS[40],
            DAYS[-1],
            initial_cash=Decimal("300000"),
            cost_settings=COSTS,
            confirmed_halts={},
            delistings=delistings,
            delisted_valuation=mode,
        )

    def test_rows_after_the_delisting_are_not_missing_rows(self):
        result = self.run_benchmark(market("AAA", "BBB"), delistings=delisting())
        self.assertEqual(result["unconfirmed_gaps"], [])
        self.assertEqual(result["equity_curve"][-1]["date"], DAYS[-1])

    def test_without_the_record_the_same_data_is_an_unconfirmed_gap(self):
        result = self.run_benchmark(market("AAA", "BBB"), delistings={})
        self.assertEqual(result["unconfirmed_gaps"][0]["first"], GONE)

    def test_benchmark_freezes_the_position_at_the_last_close(self):
        records = market("AAA", "BBB")
        result = self.run_benchmark(records, delistings=delisting())
        item = result["unrealizable"]["AAA"]
        last = records["AAA"][-1]
        self.assertEqual((item["last_close"], item["price_date"]), (last["close"], last["date"]))
        self.assertNotIn("AAA", result["holdings"])
        point = next(p for p in result["equity_curve"] if p["date"] == GONE)
        self.assertEqual(point["unrealizable_value"], item["shares"] * last["close"])
        self.assertEqual(
            point["equity"], point["cash"] + point["market_value"] + point["unrealizable_value"]
        )
        self.assertEqual([e["date"] for e in result["delisting_events"]], [GONE])

    def test_zero_valuation_starts_on_the_delisting_date(self):
        records = market("AAA", "BBB")
        frozen = self.run_benchmark(records, delistings=delisting())
        zero = self.run_benchmark(records, delistings=delisting(), mode="zero")
        for a, b in zip(frozen["equity_curve"], zero["equity_curve"], strict=True):
            if a["date"] < GONE:
                self.assertEqual(a, b)  # 終止上市日以前完全相同，不提前反映
            else:
                self.assertEqual(b["unrealizable_value"], Decimal("0"))
                self.assertEqual(a["equity"] - b["equity"], a["unrealizable_value"])

    def test_portfolio_frees_the_slot_but_not_the_money(self):
        records = market("AAA", "BBB", "CCC")
        result = self.run_portfolio(records, delistings=delisting())
        self.assertIn("AAA", result["unrealizable"])
        after = [p for p in result["equity_curve"] if p["date"] >= GONE]
        self.assertTrue(all("AAA" not in p["holdings"] for p in after))
        # 名額釋出：之後可以買進另一檔，但只用現金，無法變現的金額不能動用。
        bought_after = [t for t in result["trades"] if t["action"] == "BUY" and t["date"] > GONE]
        self.assertEqual([t["stock_id"] for t in bought_after], ["CCC"])
        for trade in bought_after:
            self.assertGreaterEqual(trade["cash_after"], Decimal("0"))
        self.assertTrue(all(p["unrealizable_value"] > 0 for p in after))

    def test_no_lookahead_before_the_delisting(self):
        records = market("AAA", "BBB", "CCC")
        with_record = self.run_portfolio(records, delistings=delisting(), end=DAYS[49])
        # 沒有清單時同樣的資料到 DAYS[49] 都有列，兩者在終止上市日以前完全相同。
        without = self.run_portfolio(records, delistings={}, end=DAYS[49])
        self.assertEqual(with_record["equity_curve"], without["equity_curve"])
        self.assertEqual(with_record["trades"], without["trades"])

    def test_rows_after_the_delisting_date_are_rejected(self):
        records = {sid: series(sid, 60) for sid in ("AAA", "BBB")}
        with self.assertRaisesRegex(ValueError, f"AAA 已於 {GONE} 終止上市，但 {GONE} 仍有行情列"):
            self.run_benchmark(records, delistings=delisting())

    def test_stocks_delisted_by_the_start_are_left_out(self):
        with TemporaryDirectory() as folder:
            db = Path(folder) / "stocks.db"
            excluded = listing_exclusions(
                ["AAA", "BBB"], db, DAYS[40], delistings=delisting(effective=DAYS[40])
            )
            self.assertEqual(
                [(e["stock_id"], e["delisted_date"]) for e in excluded], [("AAA", DAYS[40])]
            )
            self.assertIn("終止上市，不晚於回測開始日", excluded[0]["reason"])
            # 開始日之後才終止上市的照常納入。
            self.assertEqual(listing_exclusions(["AAA"], db, DAYS[40], delistings=delisting()), [])


if __name__ == "__main__":
    unittest.main()
