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
from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
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


class EngineAccountingTests(unittest.TestCase):
    """多股票引擎的公司行動帳：減資換股與除息入帳。"""

    def setUp(self):
        self.records = {"2330": rows("2330"), "2317": rows("2317")}
        self.settings = {
            "short_window": 2,
            "long_window": 5,
            "momentum_window": 3,
            "volatility_window": 3,
            "top_n": 2,
            "momentum_weight": D("1"),
            "trend_weight": D("1"),
            "volatility_weight": D("1"),
        }

    def run_engine(self, actions):
        return run_portfolio_backtest(
            self.records,
            DAYS[40],
            DAYS[-1],
            screening_settings=None,
            ranking_method="factors",
            factor_settings=self.settings,
            initial_cash=D("1000000"),
            quantity=1000,
            max_positions=2,
            cost_settings=COSTS,
            corporate_actions=actions,
        )

    def equity(self, result, day):
        return next(p for p in result["equity_curve"] if p["date"] == day)

    def test_reduction_changes_shares_and_removes_the_false_jump(self):
        raw = self.run_engine(None)
        adjusted = self.run_engine({"2330": [reduction()], "2317": []})
        before, on = DAYS[49], EVENT
        self.assertEqual(self.equity(raw, before)["holdings"].get("2330"), 1000)
        close = {r["date"]: r["close"] for r in self.records["2330"]}

        def position_return(result):
            # 2330 部位：恢復日的股數 × 收盤，加上當天入帳的公司行動現金，相對前一日。
            received = sum(
                e["cash"] for e in result["corporate_action_events"] if e["stock_id"] == "2330"
            )
            after = self.equity(result, on)["holdings"]["2330"] * close[on] + received
            return after / (self.equity(result, before)["holdings"]["2330"] * close[before]) - 1

        self.assertGreater(position_return(raw), D("0.7"))  # 股數沒換，帳面多出約八成
        self.assertLess(abs(position_return(adjusted)), D("0.01"))
        event = adjusted["corporate_action_events"][0]
        self.assertEqual(
            (event["stock_id"], event["shares_before"], event["shares_after"]), ("2330", 1000, 555)
        )
        self.assertEqual(event["fraction_cash"], 5)  # 0.556 股 × 面額 10 元
        self.assertEqual(self.equity(adjusted, on)["holdings"]["2330"], 555)
        self.assertEqual(raw["corporate_action_events"], [])

    def test_dividend_is_credited_on_the_ex_date_to_holders_at_the_previous_close(self):
        dividend = {
            "stock_id": "2317",
            "effective_date": DAYS[45],
            "kind": "dividend",
            "previous_close": D("10"),
            "reference_price": D("9.5"),
            "cash_dividend": D("0.5"),
            "stock_dividend_per_1000": D(0),
            "shares_per_1000": None,
            "cash_return": D(0),
            "needs_review": False,
        }
        result = self.run_engine({"2330": [], "2317": [dividend]})
        held = self.equity(result, DAYS[44])["holdings"].get("2317", 0)
        self.assertGreater(held, 0)
        events = [e for e in result["corporate_action_events"] if e["stock_id"] == "2317"]
        self.assertEqual(
            [(e["applied_on"], e["cash_dividend"]) for e in events], [(DAYS[45], held // 2)]
        )
        # 入帳後現金增加：除息日的現金 = 前一日現金 + 股利 ± 當日開盤成交。
        trades_on = sum(t["cash_change"] for t in result["trades"] if t["date"] == DAYS[45])
        self.assertEqual(
            self.equity(result, DAYS[45])["cash"],
            self.equity(result, DAYS[44])["cash"] + held // 2 + trades_on,
        )


class BenchmarkAccountingTests(unittest.TestCase):
    def test_buy_and_hold_gets_the_same_adjustments(self):
        records = {"2330": rows("2330"), "2317": rows("2317")}

        def run(actions):
            return run_buy_and_hold(
                records,
                DAYS[40],
                DAYS[-1],
                initial_cash=D("200000"),
                cost_settings=COSTS,
                corporate_actions=actions,
            )

        def jump(result):
            curve = {p["date"]: p for p in result["equity_curve"]}
            return curve[EVENT]["equity"] / curve[DAYS[49]]["equity"] - 1

        raw, adjusted = run(None), run({"2330": [reduction()]})
        self.assertGreater(jump(raw), D("0.3"))  # 一半資金在 2330，帳面多出約四成
        self.assertLess(abs(jump(adjusted)), D("0.01"))
        event = adjusted["corporate_action_events"][0]
        self.assertEqual((event["stock_id"], event["applied_on"]), ("2330", EVENT))
        self.assertEqual(event["shares_after"], int(event["shares_before"] * D("0.555555556")))
