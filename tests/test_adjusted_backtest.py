"""回測訊號依截至訊號日的還原價計算；成交與估值仍是原始價格。資料全為合成。"""

import copy
import json
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

from test_adjusted_research import DAYS, EVENT, reduction, rows
from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.analysis.screening_history import build_screening_history
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.strategies import from_settings
from tw_stock_backtest.strategy_comparison import compare_strategies

COSTS = CostSettings(
    commission_rate=D("0"),
    commission_discount=D("1"),
    minimum_commission=D("0"),
    sell_tax_rate=D("0"),
)


class AdjustedBacktestTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = self.root / "config.toml"
        config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', 'stocks = ["2330", "2317"]'), encoding="utf-8"
        )
        self.settings = load_config(config)
        self.records = {"2330": rows("2330"), "2317": rows("2317")}
        self.actions = {"2330": [reduction()], "2317": []}
        self.strategy = copy.deepcopy(from_settings(self.settings))
        self.strategy["conditions"] = [{"type": "breakout", "period": 20}]

    def backtest(self, actions, strategy=True):
        return run_portfolio_backtest(
            self.records,
            DAYS[40],
            DAYS[-1],
            screening_settings=self.settings["screening"],
            factor_settings=self.settings["factors"],
            ranking_method="factors",
            strategy=self.strategy if strategy else None,
            initial_cash=D("1000000"),
            quantity=10,
            max_positions=2,
            cost_settings=COSTS,
            corporate_actions=actions,
        )

    def test_signals_before_the_event_are_identical_and_the_gap_no_longer_triggers(self):
        def by_date(result):
            return {d["date"]: d["diagnostics"] for d in result["strategy_diagnostics"]}

        def breakout(days):
            return days[EVENT]["2330"]["conditions"][0]["status"]

        raw_days = by_date(self.backtest(None))
        adjusted_days = by_date(self.backtest(self.actions))
        for day in DAYS[40:50]:
            self.assertEqual(raw_days[day], adjusted_days[day], day)  # 不使用尚未生效的事件
        self.assertEqual(breakout(raw_days), "passed")
        self.assertEqual(breakout(adjusted_days), "filtered")

    def test_trades_and_valuation_still_use_original_prices(self):
        # 不加條件的因子排名，確保有成交可以比對。
        adjusted = self.backtest(self.actions, strategy=False)
        opens = {(sid, r["date"]): r["open"] for sid, rs in self.records.items() for r in rs}
        self.assertTrue(adjusted["trades"])
        for trade in adjusted["trades"]:
            self.assertEqual(trade["raw_price"], opens[(trade["stock_id"], trade["date"])])
        last = adjusted["equity_curve"][-1]
        holdings_value = sum(
            self.records[sid][-1]["close"] * shares for sid, shares in last["holdings"].items()
        )
        self.assertEqual(last["market_value"], holdings_value)

    def test_screening_history_uses_the_series_as_of_each_day(self):
        settings = {**self.settings["screening"], "min_volume_ratio": D("0")}
        settings.pop("top_n")
        raw = build_screening_history(
            self.records, DAYS[45], DAYS[52], screening_settings={**settings, "top_n": 2}
        )
        adjusted = build_screening_history(
            self.records,
            DAYS[45],
            DAYS[52],
            screening_settings={**settings, "top_n": 2},
            corporate_actions=self.actions,
        )
        raw_by, adjusted_by = ({h["date"]: h for h in x} for x in (raw, adjusted))
        for day in DAYS[45:50]:
            self.assertEqual(raw_by[day], adjusted_by[day])

        def momentum(history):
            found = [r["momentum"] for r in history[EVENT]["candidates"] if r["stock_id"] == "2330"]
            return found[0] if found else None

        self.assertGreater(momentum(raw_by), D("0.5"))
        adjusted_momentum = momentum(adjusted_by)
        self.assertTrue(adjusted_momentum is None or abs(adjusted_momentum) < D("0.1"))

    def test_strategy_comparison_records_the_price_basis(self):
        db = self.settings["storage"]["database_path"]
        save_records(self.records["2330"] + self.records["2317"], db_path=db)
        ca.save_actions(
            [reduction()],
            db,
            fetched_at="t",
            coverage=[(s, "2026-01-01", "2026-12-31") for s in ("2330", "2317")],
        )
        other = copy.deepcopy(self.strategy)
        other["id"], other["name"] = "other", "其他"
        path = compare_strategies(
            self.settings, [self.strategy, other], DAYS[40], DAYS[-1], self.root / "out"
        )
        common = json.loads((Path(path) / "common.json").read_text(encoding="utf-8"))["execution"]
        self.assertEqual(common["price_adjustment"]["mode"], "adjusted")
        self.assertEqual(
            [a["effective_date"] for a in common["price_adjustment"]["actions"]], [EVENT]
        )


if __name__ == "__main__":
    unittest.main()
