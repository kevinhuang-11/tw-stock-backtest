"""現金增資 decline（放棄認購）：股數不因認購增加、不付款，稀釋損失只列出不另外扣除。

資料為合成。純現金增資的條件仿 test_rights_offerings（2026-08-18 的 6225）；
另一筆同時配發現金股利與股票股利，用來核對減除股利參考價與稀釋損失的算法。
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_adjusted_research import DAYS, rows
from test_end_to_end import TEST_CONFIG
from test_rights_offerings import COSTS, EX_RIGHTS, rights
from test_ui_pages import PageCase, write

from tw_stock_backtest.analysis.adjustment import (
    AdjustedHistory,
    adjust,
    events,
    ex_dividend_reference,
)
from tw_stock_backtest.analysis.forward_returns import evaluate_forward_return
from tw_stock_backtest.backtesting.backtest import run_backtest
from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.corporate_actions import RightsOfferingHeld, entitlement
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.cli import run_portfolio
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.prices import PriceSource, describe_prices
from tw_stock_backtest.research_digest import differences
from tw_stock_backtest.watchlist_research import compare
from tw_stock_backtest.workbench import compare_rankings

FACTORS = {
    "short_window": 2,
    "long_window": 5,
    "momentum_window": 3,
    "volatility_window": 3,
    "top_n": 2,
    "momentum_weight": D("1"),
    "trend_weight": D("1"),
    "volatility_weight": D("1"),
}


def declined(**changes):
    return {**rights(), "rights": "decline", **changes}


def combined():
    """前收 50、每股現金股利 1、每千股配股 100、每千股認購 200 股、認購價 30。

    官方參考價 =（50 − 1 ＋ 30 × 0.2）÷（1 ＋ 0.1 ＋ 0.2）= 42.307…，公布 42.30；
    減除股利參考價 =（50 − 1）÷ 1.1 = 44.545…。
    """
    return declined(
        previous_close=D("50"),
        reference_price=D("42.30"),
        cash_dividend=D("1"),
        stock_dividend_per_1000=D("100"),
        rights_per_1000=D("200"),
        rights_price=D("30"),
    )


class AccountingTests(unittest.TestCase):
    def test_declined_rights_keep_dividends_and_list_the_dilution(self):
        detail = entitlement(1000, combined())
        self.assertEqual(detail["shares_after"], 1100)  # 只有無償配股
        self.assertEqual(detail["cash"], D("1000"))  # 只有現金股利，不付認購款
        self.assertEqual(detail["rights"], "decline")
        self.assertEqual(detail["rights_shares"], D("200"))
        self.assertEqual(detail["rights_price"], D("30"))
        self.assertEqual(round(detail["ex_dividend_reference"], 6), D("44.545455"))
        # 1000 × 1.1 ×（44.5454… − 42.30）= 2470
        self.assertEqual(detail["dilution_loss"], D("2470"))

    def test_pure_rights_offering_loses_the_drop_to_the_reference(self):
        detail = entitlement(1000, declined())
        self.assertEqual((detail["shares_after"], detail["cash"]), (1000, D("0")))
        self.assertEqual(detail["ex_dividend_reference"], D("10"))
        self.assertEqual(detail["dilution_loss"], D("3200"))  # 1000 ×（10 − 6.8）

    def test_unmarked_actions_are_still_refused(self):
        self.assertNotIn("rights", entitlement(1000, rights()))
        with self.assertRaises(RightsOfferingHeld) as caught:
            run_buy_and_hold(
                {"2317": rows("2317")},
                DAYS[40],
                DAYS[-1],
                initial_cash=D("100000"),
                cost_settings=COSTS,
                corporate_actions={"2317": [rights()]},
            )
        self.assertIn('corporate_actions.rights = "decline"', str(caught.exception))


class BacktestTests(unittest.TestCase):
    def test_buy_and_hold_completes_without_an_extra_deduction(self):
        records = {"2317": rows("2317")}
        kwargs = dict(initial_cash=D("100000"), cost_settings=COSTS)
        result = run_buy_and_hold(
            records, DAYS[40], DAYS[-1], corporate_actions={"2317": [declined()]}, **kwargs
        )
        (event,) = result["corporate_action_events"]
        self.assertEqual(event["applied_on"], EX_RIGHTS)
        self.assertEqual(event["shares_before"], event["shares_after"])
        self.assertEqual(event["dilution_loss"], D(event["shares_before"]) * D("3.2"))
        # 純現金增資：持股與現金都不變，資產和不計公司行動時完全相同（損失已在市價中）。
        raw = run_buy_and_hold(records, DAYS[40], DAYS[-1], **kwargs)
        self.assertEqual(
            [p["equity"] for p in result["equity_curve"]],
            [p["equity"] for p in raw["equity_curve"]],
        )

    def test_portfolio_and_single_engine_complete(self):
        records = {"2317": rows("2317"), "1101": rows("1101")}
        result = run_portfolio_backtest(
            records,
            DAYS[40],
            DAYS[-1],
            screening_settings=None,
            ranking_method="factors",
            factor_settings=FACTORS,
            initial_cash=D("1000000"),
            quantity=1000,
            max_positions=2,
            cost_settings=COSTS,
            corporate_actions={"2317": [declined()]},
        )
        self.assertIn(
            ("2317", EX_RIGHTS, "decline"),
            [
                (e["stock_id"], e["applied_on"], e["rights"])
                for e in result["corporate_action_events"]
            ],
        )
        single = run_backtest(
            rows("2317"),
            ["BUY"] + ["NONE"] * 59,
            D("100000"),
            1000,
            corporate_actions=[declined()],
        )
        self.assertEqual(single["corporate_action_events"][0]["dilution_loss"], D("3200"))


class IndicatorTests(unittest.TestCase):
    def test_pure_rights_offering_is_not_adjusted_and_does_not_restart(self):
        series = rows("2317")
        self.assertEqual(adjust(series, [declined()], DAYS[50]), adjust(series, [], DAYS[50]))

    def test_dividends_in_the_same_action_use_the_ex_dividend_reference(self):
        action = combined()
        ((day, price, shares),) = events([action])
        self.assertEqual(day, EX_RIGHTS)
        self.assertEqual(price, ex_dividend_reference(action) / D("50"))
        self.assertEqual(shares, D("1.1"))
        series = rows("2317")
        after = adjust(series, [action], DAYS[50])
        self.assertEqual(after[44]["close"], series[44]["close"] * price)
        self.assertEqual(after[44]["volume"], 1100)
        self.assertEqual(after[45]["close"], series[45]["close"])

    def test_no_lookahead_before_the_ex_rights_date(self):
        series = rows("2317")
        for action in (declined(), combined()):
            for index in range(45):
                day = DAYS[index]
                self.assertEqual(adjust(series, [action], day), adjust(series, [], day))
            history = AdjustedHistory([action])
            for index, record in enumerate(series):
                history.append(record)
                self.assertEqual(
                    history.rows, adjust(series[: index + 1], [action], record["date"])
                )

    def test_backtest_before_the_ex_rights_date_matches_raw(self):
        records = {"2317": rows("2317"), "1101": rows("1101")}

        def run(actions):
            return run_portfolio_backtest(
                records,
                DAYS[30],
                DAYS[44],
                screening_settings=None,
                ranking_method="factors",
                factor_settings=FACTORS,
                initial_cash=D("1000000"),
                quantity=1000,
                max_positions=2,
                cost_settings=COSTS,
                corporate_actions=actions,
            )

        declined_run, raw_run = run({"2317": [combined()]}), run({})
        self.assertIn("2317", {t["stock_id"] for t in declined_run["trades"]})
        self.assertEqual(declined_run["trades"], raw_run["trades"])
        self.assertEqual(declined_run["equity_curve"], raw_run["equity_curve"])


class ForwardReturnTests(unittest.TestCase):
    def test_declined_rights_count_only_dividends(self):
        series = rows("2317")
        entry, exit_ = series[43]["open"], series[47]["close"]
        pure = evaluate_forward_return(
            series, DAYS[42], DAYS, horizon=5, corporate_actions=[declined()]
        )
        self.assertEqual(pure["status"], "ok")
        self.assertEqual(pure["price_return"], exit_ / entry - 1)
        both = evaluate_forward_return(
            series, DAYS[42], DAYS, horizon=5, corporate_actions=[combined()]
        )
        self.assertEqual(both["price_return"], (exit_ * D("1.1") + D("1")) / entry - 1)


class PriceSourceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', 'stocks = ["2317"]'), encoding="utf-8"
        )
        self.settings = load_config(self.config)
        self.db = self.settings["storage"]["database_path"]
        save_records(rows("2317"), db_path=self.db)
        action = {
            **rights(),
            "halt_start": None,
            "review_notes": [],
            "sources": ["https://www.twse.com.tw/example"],
            "raw": {},
        }
        ca.save_actions(
            [action], self.db, fetched_at="t", coverage=[("2317", "2026-01-01", "2026-12-31")]
        )

    def source(self, rights_mode):
        return PriceSource(
            {**self.settings, "corporate_actions": {"mode": "adjusted", "rights": rights_mode}}
        )

    def test_actions_carry_the_setting_and_the_summary_records_it(self):
        refuse, decline = self.source("refuse"), self.source("decline")
        self.assertEqual(decline.actions("2317")[0]["rights"], "decline")
        for source in (refuse, decline):
            source.load("2317", "1900-01-01", DAYS[-1])
        self.assertEqual(refuse.describe()["rights"], "refuse")
        self.assertEqual(len(refuse.describe()["restarts"]), 1)
        self.assertEqual(decline.describe()["rights"], "decline")
        self.assertEqual(decline.describe()["restarts"], [])
        self.assertEqual(decline.restart_note("2317", DAYS[-1]), "")
        self.assertIn("現金增資：放棄認購（decline）", describe_prices(decline.describe()))
        self.assertIn("放棄認購", describe_prices(decline.describe(), backtest=True))
        self.assertIn("拒絕計算", describe_prices(refuse.describe(), backtest=True))

    def run_cli(self, rights_mode):
        self.config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', 'stocks = ["2317"]')
            + f'\n[corporate_actions]\nmode = "adjusted"\nrights = "{rights_mode}"\n',
            encoding="utf-8",
        )
        output = io.StringIO()
        argv = [
            "run_portfolio",
            "--config",
            str(self.config),
            "--start",
            DAYS[40],
            "--end",
            DAYS[-1],
            "--export",
            "--output-dir",
            str(self.root / rights_mode),
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            return run_portfolio.main(), output.getvalue()

    def test_cli_refuses_by_default_and_completes_with_decline(self):
        code, output = self.run_cli("refuse")
        self.assertEqual(code, 1)
        self.assertIn("現金增資除權", output)

        code, output = self.run_cli("decline")
        self.assertEqual(code, 0, output)
        self.assertIn("現金增資：放棄認購（decline）", output)
        self.assertIn("放棄認購的稀釋損失合計（已反映在市價）", output)
        (folder,) = (self.root / "decline").iterdir()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["strategy"]["result"]["price_adjustment"]["rights"], "decline")
        (event,) = results["benchmark"]["result"]["corporate_action_events"]
        self.assertEqual(event["rights"], "decline")


def research(rights_mode=None):
    detail = {
        "kind": "research",
        "status": "success",
        "rankings": [{"stock_id": "2330", "rank": 1}],
    }
    detail["price_adjustment"] = {"mode": "adjusted", "actions": [], "uncovered": {}}
    if rights_mode:
        detail["price_adjustment"]["rights"] = rights_mode
    return detail


class ComparisonGuardTests(unittest.TestCase):
    def test_research_reports_with_different_rights_modes_are_not_compared(self):
        # 舊報表沒有記錄 rights，當時只有 refuse。
        self.assertEqual(len(compare_rankings(research(), research("refuse"))), 1)
        with self.assertRaisesRegex(ValueError, "現金增資處理方式不同"):
            compare_rankings(research("refuse"), research("decline"))

    def test_digest_and_watchlist_do_not_compare_across_rights_modes(self):
        def bundle(rights_mode):
            summary = {"ranking_population": ["2330"], **research(rights_mode)}
            return {"summary": summary, "settings": {}, "mode": "factors", "universe": {}}

        self.assertIn(
            "現金增資處理方式不同（拒絕計算／放棄認購），不比較名次",
            differences(bundle("decline"), bundle(None)),
        )

        def snapshot(rights_mode, status):
            return {
                "run_id": "r",
                "specified_pool": ["2330"],
                "strategy_snapshot": {"sha256": "a"},
                "requested_date": None,
                "rows": [{"stock_id": "2330", "status": status}],
                "price_adjustment": research(rights_mode)["price_adjustment"],
            }

        mixed = compare(snapshot("decline", "eligible"), snapshot("refuse", "filtered"))
        self.assertIn("價格口徑不同", mixed["message"])
        self.assertEqual(mixed["changes"], [])


class ReportPageTests(PageCase):
    def page(self):
        key = next(iter(self.workbench.catalog()))
        return self.client.get(f"/reports/{key}").text

    def test_backtest_page_labels_the_rights_mode_and_lists_the_dilution(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        event = {
            "applied_on": EX_RIGHTS,
            "stock_id": "6225",
            "kind": "dividend",
            "shares_before": 1000,
            "shares_after": 1000,
            "cash": "0",
            "needs_review": True,
            "rights": "decline",
            "rights_shares": "868.70503597",
            "rights_price": "17.65",
            "ex_dividend_reference": "44.40",
            "reference_price": "30.04",
            "dilution_loss": "14360",
        }
        results["strategy"]["result"] = {
            "price_adjustment": {"mode": "adjusted", "rights": "decline", "actions": []},
            "corporate_action_events": [event],
        }
        results["benchmark"]["result"] = {"corporate_action_events": [event, event]}
        write(folder, "results.json", results)
        text = self.page()
        self.assertIn("現金增資：放棄認購（decline）", text)
        self.assertIn("<h2>放棄認購的稀釋損失</h2>", text)
        self.assertIn("策略合計 14,360 元，基準合計 28,720 元", text)
        self.assertIn("（含現金增資，放棄認購）", text)

    def test_reports_without_the_setting_are_labelled_refuse(self):
        folder = self.add_backtest()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        results["strategy"]["result"] = {"price_adjustment": {"mode": "adjusted", "actions": []}}
        write(folder, "results.json", results)
        text = self.page()
        self.assertIn("現金增資：拒絕計算（refuse）", text)
        self.assertIn("持有跨過含現金增資的除權時拒絕計算", text)
        self.assertNotIn("放棄認購的稀釋損失", text)


if __name__ == "__main__":
    unittest.main()
