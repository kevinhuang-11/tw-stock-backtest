"""docs/HALT-POLICY.md 的情況編號與測試對照。

CASES 列出每個情況（3.1～3.24）由哪些測試涵蓋；PolicyMappingTests 確認文件的每個編號都在
對照表中、每個測試都存在。對照表以外沒有專屬測試的情況，直接在 PolicyCaseTests 測。
全部使用合成資料。
"""

import importlib
import re
import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.costs import CostSettings

POLICY = Path(__file__).resolve().parents[1] / "docs" / "HALT-POLICY.md"

CASES = {
    "3.1": [
        "test_confirmed_halts.HaltRowsTests.test_confirmed_halt_days_get_halt_rows_without_prices",
        "test_confirmed_halts.HaltRowsTests.test_backtest_runs_through_the_halt_instead_of_failing",
        "test_halt_continuation.TestHaltContinuation.test_other_stock_trades_and_halted_stock_resumes",
        "test_market_events.TestMarketEvents.test_future_price_is_not_used_for_halt_valuation",
        "test_missing_prices.TestBacktestWithMissingCloses.test_a_missing_confirmed_halt_day_is_still_a_halt",
    ],
    "3.2": [
        "test_halt_policy.PolicyCaseTests.test_3_02_partial_day_pause_trades_normally",
        "test_market_events.TestMarketEvents.test_conflicting_event_and_prices_are_rejected",
    ],
    "3.3": [
        "test_delistings.FileTests.test_packaged_cases_are_verified_with_official_sources",
        "test_delistings.BacktestTests.test_rows_after_the_delisting_are_not_missing_rows",
        "test_halt_reports.FinalPositionTests.test_announced_delisting_is_shown_only_after_the_announcement",
    ],
    "3.4": [
        "test_halt_policy.PolicyCaseTests.test_3_04_share_swap_delisting_lists_the_kind_and_consideration",
    ],
    "3.5": [
        "test_market_events.TestMarketEvents.test_unconfirmed_missing_prices_are_no_price_not_halt",
        "test_missing_prices.TestBacktestWithMissingCloses.test_unconfirmed_unpriced_day_inside_the_period_is_not_tradable_and_reported",
        "test_missing_prices.TestSingleStockWithMissingCloses.test_engine_values_a_held_position_at_the_last_close_on_a_no_price_day",
    ],
    "3.6": [
        "test_missing_prices.TestBacktestWithMissingCloses.test_missing_row_is_unconfirmed_not_tradable_and_reported",
        "test_listing_universe.ListingUniverseTests.test_comparison_and_selection_report_unconfirmed_gaps",
        "test_listing_universe.ExclusionPageTests.test_backtest_page_lists_unconfirmed_gaps",
    ],
    "3.7": [
        "test_missing_prices.TestBacktestWithMissingCloses.test_many_missing_rows_on_one_day_mean_the_data_is_not_updated",
        "test_missing_prices.TestBacktestWithMissingCloses.test_the_batch_threshold_is_more_than_ten_percent_and_at_least_two",
        "test_screen_factors_cli.TestAnalysisDateMessages.test_a_day_without_any_market_rows_does_not_look_ahead",
    ],
    "3.8": [
        "test_missing_prices.TestBacktestWithMissingCloses.test_a_stock_without_any_rows_was_never_downloaded",
    ],
    "3.9": [
        "test_listing_universe.ListingUniverseTests.test_backtest_excludes_late_listings_and_records_why",
        "test_listing_universe.ListingUniverseTests.test_selection_evaluation_leaves_out_late_listings",
    ],
    "3.10": [
        "test_delistings.BacktestTests.test_stocks_delisted_by_the_start_are_left_out",
        "test_delistings.BacktestTests.test_without_the_record_the_same_data_is_an_unconfirmed_gap",
    ],
    "3.11": [
        "test_halt_policy.PolicyCaseTests.test_3_11_market_holiday_is_not_a_missing_row",
        "test_data_quality_sql.SqlMatchesPythonTests.test_market_lag_for_every_kind_of_day",
    ],
    "3.12": [
        "test_halt_reports.ResearchReasonTests.test_confirmed_halt_on_the_analysis_date",
        "test_halt_reports.ResearchReasonTests.test_no_price_on_the_analysis_date",
        "test_screen_factors_cli.TestAnalysisDateMessages.test_a_missing_row_when_others_trade_is_unconfirmed",
        "test_halt_continuation.TestHaltContinuation.test_halted_holding_still_uses_position_slot",
    ],
    "3.13": [
        "test_halt_continuation.TestHaltContinuation.test_benchmark_delays_only_the_halted_stock",
        "test_benchmark.TestBenchmarkSlippage.test_delayed_buy_uses_resumption_open",
        "test_halt_reports.ReportPageTests.test_final_positions_and_benchmark_gaps_are_listed",
    ],
    "3.14": [
        "test_halt_reports.FinalPositionTests.test_halted_position_at_the_end_is_labelled_with_its_price_date",
    ],
    "3.15": [
        "test_sell_intent.PortfolioTests.test_sell_decided_before_the_halt_executes_at_the_first_tradable_open",
        "test_sell_intent.PortfolioTests.test_the_halted_holding_keeps_its_slot_until_sold",
        "test_portfolio_backtest.TestPortfolioBudget.test_halted_sell_does_not_release_slot_or_cash",
    ],
    "3.16": [
        "test_delistings.BacktestTests.test_benchmark_freezes_the_position_at_the_last_close",
        "test_delistings.BacktestTests.test_zero_valuation_starts_on_the_delisting_date",
        "test_delistings.BacktestTests.test_portfolio_frees_the_slot_but_not_the_money",
        "test_delistings.BacktestTests.test_no_lookahead_before_the_delisting",
    ],
    "3.17": [
        "test_restrictions.ParseTests.test_dispositions_keep_stocks_and_parse_the_period",
        "test_restrictions.StoreTests.test_active_labels_only_after_the_announcement_and_within_the_period",
        "test_restrictions.StoreTests.test_labels_do_not_change_numbers",
    ],
    "3.18": [
        "test_restrictions.ParseTests.test_full_delivery_list_marks_split_auction",
        "test_restrictions.StoreTests.test_daily_lists_are_merged_into_consecutive_periods",
    ],
    "3.19": [
        "test_halt_policy.PolicyCaseTests.test_3_19_limit_lock_is_not_simulated",
    ],
    "3.20": [
        "test_halt_policy.PolicyCaseTests.test_3_20_reduction_applies_on_the_resumption_day",
        "test_adjusted_backtest.EngineAccountingTests.test_reduction_changes_shares_and_removes_the_false_jump",
    ],
    "3.21": [
        "test_market_events.TestMarketEvents.test_conflicting_event_and_prices_are_rejected",
        "test_confirmed_halts.HaltRowsTests.test_existing_rows_are_never_replaced",
        "test_delistings.BacktestTests.test_rows_after_the_delisting_date_are_rejected",
    ],
    "3.22": [
        "test_forward_returns.TestForwardReturn.test_halted_entry_does_not_delay_entry",
        "test_forward_returns.TestForwardReturn.test_halted_exit_uses_the_last_close_and_says_so",
        "test_forward_returns.TestForwardReturn.test_delisted_exit_follows_the_valuation_setting",
        "test_forward_returns.TestRankingEvaluation.test_stale_exits_are_counted_in_valid_samples",
    ],
    "3.23": [
        "test_sell_intent.SingleEngineTests.test_sell_signal_is_kept_until_the_next_tradable_open",
        "test_sell_intent.SingleEngineTests.test_buy_signal_on_an_untradable_day_is_dropped",
    ],
    "3.24": [
        "test_missing_prices.TestBacktestWithMissingCloses.test_unconfirmed_unpriced_row_before_the_period_does_not_fail_the_backtest",
        "test_missing_prices.TestBacktestWithMissingCloses.test_missing_row_in_warmup_is_not_filled",
        "test_missing_prices.TestBacktestWithMissingCloses.test_strategy_rsi_condition_recovers_after_a_confirmed_halt_in_warmup",
    ],
}


class PolicyMappingTests(unittest.TestCase):
    @unittest.skipUnless(POLICY.is_file(), "只有原始碼樹裡有 docs/HALT-POLICY.md")
    def test_every_case_in_the_policy_has_tests(self):
        numbers = re.findall(r"^### (3\.\d+) ", POLICY.read_text(encoding="utf-8"), re.M)
        self.assertEqual(numbers, list(CASES))

    def test_every_listed_test_exists(self):
        for case, names in CASES.items():
            self.assertTrue(names, case)
            for name in names:
                with self.subTest(case=case, test=name):
                    module, cls, method = name.split(".")
                    test_class = getattr(importlib.import_module(module), cls)
                    self.assertTrue(callable(getattr(test_class, method, None)), name)


COSTS = CostSettings(
    commission_rate=D(0), commission_discount=D(1), minimum_commission=D(0), sell_tax_rate=D(0)
)


def days(count, first=date(2026, 8, 3)):
    result, current = [], first
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


DAYS = days(12)


def row(stock_id, day, open_price, close=None, high=None):
    close = open_price if close is None else close
    high = max(open_price, close) if high is None else high
    return {
        "stock_id": stock_id,
        "date": day,
        "open": D(open_price),
        "high": D(high),
        "low": D(min(open_price, close)),
        "close": D(close),
        "volume": 1000,
        "turnover": 1,
        "trade_count": 1,
    }


def halted(stock_id, day):
    return {
        "stock_id": stock_id,
        "date": day,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": 0,
        "turnover": 0,
        "trade_count": 0,
    }


def buy_and_hold(records, *, halts=None, delistings=None, actions=None, start=DAYS[0]):
    return run_buy_and_hold(
        records,
        start,
        max(r["date"] for rows in records.values() for r in rows),
        initial_cash=D(100000),
        cost_settings=COSTS,
        confirmed_halts=halts or {},
        delistings=delistings or {},
        corporate_actions=actions,
    )


class PolicyCaseTests(unittest.TestCase):
    def test_3_02_partial_day_pause_trades_normally(self):
        # 盤中暫停後同日恢復：來源有價格，不在停牌清單，當日視為正常並以開盤價成交。
        records = {"AAA": [row("AAA", d, 10) for d in DAYS[:3]]}
        records["AAA"][0] = row("AAA", DAYS[0], 10, close=11, high=12)
        result = buy_and_hold(records)
        self.assertEqual(result["trades"][0]["date"], DAYS[0])
        self.assertEqual(result["trades"][0]["price"], D(10))

    def test_3_04_share_swap_delisting_lists_the_kind_and_consideration(self):
        records = {sid: [row(sid, d, 10) for d in DAYS[:6]] for sid in ("AAA", "BBB")}
        records["AAA"] = records["AAA"][:4]
        delistings = {
            "AAA": {
                "stock_id": "AAA",
                "announced": DAYS[1],
                "effective": DAYS[4],
                "kind": "share_swap",
                "reason": "股份轉換",
                "consideration": "依股份轉換契約",
                "sources": ["https://x"],
            }
        }
        result = buy_and_hold(records, delistings=delistings)
        item = result["unrealizable"]["AAA"]
        self.assertEqual(item["kind_label"], "股份轉換")
        self.assertEqual(item["consideration"], "依股份轉換契約")
        # 對價不模擬入帳：仍以最後收盤凍結估值。
        self.assertEqual(item["last_close"], D(10))

    def test_3_11_market_holiday_is_not_a_missing_row(self):
        # DAYS[2] 全市場都沒有列：不是行情日，不產生缺列，也不出現在資產曲線。
        records = {
            sid: [row(sid, d, 10) for i, d in enumerate(DAYS[:5]) if i != 2]
            for sid in ("AAA", "BBB")
        }
        result = buy_and_hold(records)
        self.assertEqual(result["unconfirmed_gaps"], [])
        self.assertNotIn(DAYS[2], [p["date"] for p in result["equity_curve"]])

    def test_3_19_limit_lock_is_not_simulated(self):
        # 開盤即在當日最高價（例如漲停鎖死）：v1 仍以開盤價成交，這是已知的簡化。
        records = {"AAA": [row("AAA", d, 10) for d in DAYS[:3]]}
        records["AAA"][0] = row("AAA", DAYS[0], 11, close=11, high=11)
        result = buy_and_hold(records)
        self.assertEqual(result["trades"][0]["price"], D(11))

    def test_3_20_reduction_applies_on_the_resumption_day(self):
        # 減資：停牌期間以舊股數、停牌前收盤估值；恢復買賣日開盤前換股。
        records = {
            "AAA": [row("AAA", d, 10) for d in DAYS[:3]]
            + [halted("AAA", d) for d in DAYS[3:6]]
            + [row("AAA", d, 18) for d in DAYS[6:9]],
            "BBB": [row("BBB", d, 10) for d in DAYS[:9]],
        }
        halts = {
            ("AAA", d): {"reason": "減資", "source": "https://x", "sources": []} for d in DAYS[3:6]
        }
        reduction = {
            "stock_id": "AAA",
            "effective_date": DAYS[6],
            "kind": "capital_reduction",
            "previous_close": D(10),
            "reference_price": D(18),
            "cash_dividend": D(0),
            "stock_dividend_per_1000": D(0),
            "shares_per_1000": D(555),
            "cash_return": D(0),
            "rights_per_1000": D(0),
            "rights_price": D(0),
            "needs_review": False,
        }
        result = buy_and_hold(records, halts=halts, actions={"AAA": [reduction]})
        shares = result["trades"][0]["quantity"]
        during = next(p for p in result["equity_curve"] if p["date"] == DAYS[4])
        self.assertEqual(during["holdings"]["AAA"], shares)
        self.assertEqual(during["stale_prices"]["AAA"], DAYS[2])
        resumed = next(p for p in result["equity_curve"] if p["date"] == DAYS[6])
        self.assertEqual(resumed["holdings"]["AAA"], shares * 555 // 1000)
        self.assertEqual(result["corporate_action_events"][0]["applied_on"], DAYS[6])


if __name__ == "__main__":
    unittest.main()
