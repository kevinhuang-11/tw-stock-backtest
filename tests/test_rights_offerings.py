"""含現金增資的除權：還原價不跨過生效日，回測持有跨過時拒絕計算。資料為合成，條件仿 2026-08-18 的 6225。"""

import unittest
from decimal import Decimal as D

from test_adjusted_research import DAYS, rows

from tw_stock_backtest.analysis.adjustment import AdjustedHistory, adjust
from tw_stock_backtest.analysis.factors import calculate_factor_row
from tw_stock_backtest.analysis.forward_returns import evaluate_forward_return
from tw_stock_backtest.backtesting.backtest import run_backtest
from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.corporate_actions import RightsOfferingHeld
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest

COSTS = CostSettings(
    commission_rate=D("0"),
    commission_discount=D("1"),
    minimum_commission=D("0"),
    sell_tax_rate=D("0"),
)
EX_RIGHTS = DAYS[45]


def rights(stock_id="2317", day=EX_RIGHTS):
    return {
        "stock_id": stock_id,
        "effective_date": day,
        "kind": "dividend",
        "previous_close": D("10"),
        "reference_price": D("6.8"),
        "cash_dividend": D(0),
        "stock_dividend_per_1000": D(0),
        "shares_per_1000": None,
        "cash_return": D(0),
        "rights_per_1000": D("868.70503597"),
        "rights_price": D("17.65"),
        "needs_review": True,
    }


class IndicatorTests(unittest.TestCase):
    def test_series_restarts_on_the_ex_rights_date(self):
        series = rows("2317")
        before = adjust(series, [rights()], as_of=DAYS[44])
        self.assertEqual(len(before), 45)  # 生效日以前不受影響
        after = adjust(series, [rights()], as_of=DAYS[50])
        self.assertEqual([r["date"] for r in after], DAYS[45:51])
        self.assertEqual(after[0]["close"], series[45]["close"])  # 不依官方參考價縮放

    def test_windowed_factors_are_unavailable_until_enough_rows_follow(self):
        settings = {
            "short_window": 2,
            "long_window": 5,
            "momentum_window": 3,
            "volatility_window": 3,
        }
        with self.assertRaisesRegex(ValueError, "至少需要 5 筆行情"):
            calculate_factor_row(
                adjust(rows("2317"), [rights()], DAYS[47]), DAYS[47], factor_settings=settings
            )
        calculate_factor_row(
            adjust(rows("2317"), [rights()], DAYS[49]), DAYS[49], factor_settings=settings
        )

    def test_incremental_history_matches_adjust(self):
        series = rows("2317")
        history = AdjustedHistory([rights()])
        for index, record in enumerate(series):
            history.append(record)
            self.assertEqual(history.rows, adjust(series[: index + 1], [rights()], record["date"]))


class BacktestTests(unittest.TestCase):
    def test_holding_across_the_ex_rights_date_is_refused(self):
        records = {"2317": rows("2317"), "1101": rows("1101")}
        factors = {
            "short_window": 2,
            "long_window": 5,
            "momentum_window": 3,
            "volatility_window": 3,
            "top_n": 2,
            "momentum_weight": D("1"),
            "trend_weight": D("1"),
            "volatility_weight": D("1"),
        }
        with self.assertRaises(RightsOfferingHeld) as caught:
            run_portfolio_backtest(
                records,
                DAYS[40],
                DAYS[-1],
                screening_settings=None,
                ranking_method="factors",
                factor_settings=factors,
                initial_cash=D("1000000"),
                quantity=1000,
                max_positions=2,
                cost_settings=COSTS,
                corporate_actions={"2317": [rights()]},
            )
        message = str(caught.exception)
        self.assertIn(f"持有 2317 跨過 {EX_RIGHTS} 的現金增資除權", message)
        self.assertIn("每千股可認購 868.70503597 股", message)
        self.assertIn("改用 raw 模式", message)

    def test_benchmark_and_single_engine_refuse_too(self):
        records = {"2317": rows("2317")}
        with self.assertRaises(RightsOfferingHeld):
            run_buy_and_hold(
                records,
                DAYS[40],
                DAYS[-1],
                initial_cash=D("100000"),
                cost_settings=COSTS,
                corporate_actions={"2317": [rights()]},
            )
        signals = ["BUY"] + ["NONE"] * 59
        with self.assertRaises(RightsOfferingHeld):
            run_backtest(rows("2317"), signals, D("100000"), 1000, corporate_actions=[rights()])

    def test_not_holding_across_the_date_is_fine_and_raw_mode_is_unchanged(self):
        # 期間在生效日之後開始，持股從未跨過生效日。
        result = run_buy_and_hold(
            {"2317": rows("2317")},
            DAYS[46],
            DAYS[-1],
            initial_cash=D("100000"),
            cost_settings=COSTS,
            corporate_actions={"2317": [rights()]},
        )
        self.assertEqual(result["corporate_action_events"], [])
        raw = run_buy_and_hold(
            {"2317": rows("2317")},
            DAYS[40],
            DAYS[-1],
            initial_cash=D("100000"),
            cost_settings=COSTS,
        )
        self.assertEqual(raw["corporate_action_events"], [])


class ForwardReturnTests(unittest.TestCase):
    def test_crossing_a_rights_offering_is_not_evaluated(self):
        result = evaluate_forward_return(
            rows("2317"), DAYS[42], DAYS, horizon=5, corporate_actions=[rights()]
        )
        self.assertEqual((result["status"], result["reason"]), ("unavailable", "rights_offering"))
        clear = evaluate_forward_return(
            rows("2317"), DAYS[46], DAYS, horizon=5, corporate_actions=[rights()]
        )
        self.assertEqual(clear["status"], "ok")


if __name__ == "__main__":
    unittest.main()


class ResearchNoteTests(unittest.TestCase):
    def test_exclusion_reason_and_summary_mention_the_restart(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from test_end_to_end import TEST_CONFIG

        from tw_stock_backtest.cli.screen_factors import collect_factor_rows
        from tw_stock_backtest.config import load_config
        from tw_stock_backtest.data import corporate_actions as ca
        from tw_stock_backtest.data.database import save_records
        from tw_stock_backtest.prices import PriceSource

        with TemporaryDirectory() as folder:
            config = Path(folder) / "config.toml"
            config.write_text(TEST_CONFIG, encoding="utf-8")
            settings = load_config(config)
            db = settings["storage"]["database_path"]
            save_records(rows("2317"), db_path=db)
            action = {
                **rights(),
                "rights_per_1000": D("868.70503597"),
                "halt_start": None,
                "review_notes": [],
                "sources": ["https://www.twse.com.tw/example"],
                "raw": {},
            }
            ca.save_actions(
                [action], db, fetched_at="t", coverage=[("2317", "2026-01-01", "2026-12-31")]
            )
            prices = PriceSource(settings)
            _, errors = collect_factor_rows(
                [{"stock_id": "2317"}],
                DAYS[47],
                db_path=db,
                factor_settings=settings["factors"],
                prices=prices,
            )
            self.assertIn(
                f"（{EX_RIGHTS} 含現金增資，還原價從這一天重新起算）", errors[0]["reason"]
            )
            self.assertEqual(
                [r["effective_date"] for r in prices.describe()["restarts"]], [EX_RIGHTS]
            )
