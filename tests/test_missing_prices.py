"""缺價對指標與回測的影響；全部使用合成資料。

兩種模式：
- 已確認停牌：開高低收皆空，量、值、筆數為 0，且列在停牌事件中。
- 有量無價：來源有該日，但開高低收為空（例如只有零星零股成交），未確認為停牌。
"""

import contextlib
import io
import re
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.analysis.factors import calculate_factor_row
from tw_stock_backtest.analysis.indicators import (
    simple_moving_average,
    window_gap_reason,
    windowed_moving_average,
)
from tw_stock_backtest.analysis.screening import evaluate_stock
from tw_stock_backtest.backtesting.backtest import run_backtest
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.cli import analyze_stock
from tw_stock_backtest.cli import run_backtest as run_backtest_cli
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.strategies import evaluate_conditions

RSI = {"type": "rsi", "period": 14, "minimum": "0", "maximum": "100"}
MACD = {"type": "macd", "fast": 12, "slow": 26, "signal": 9}


def trading_days(count, first=date(2026, 1, 5)):
    days, current = [], first
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def priced(stock_id, day, index):
    close = Decimal(100 + (index * 7) % 11 - 5)
    return {
        "stock_id": stock_id,
        "date": day,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000 + index,
        "turnover": 100000,
        "trade_count": 50,
    }


def unpriced(stock_id, day, volume=0, trade_count=0):
    return {
        "stock_id": stock_id,
        "date": day,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": volume,
        "turnover": volume * 100,
        "trade_count": trade_count,
    }


def series(stock_id, count, gaps=None):
    """gaps: {位置: 缺價列的量與筆數}；其餘位置為正常行情。"""
    gaps = gaps or {}
    return [
        unpriced(stock_id, day, *gaps[i]) if i in gaps else priced(stock_id, day, i)
        for i, day in enumerate(trading_days(count))
    ]


def status(records, as_of, condition):
    return evaluate_conditions(records, as_of, [condition])[0]


class TestRecursiveIndicatorsAfterMissingClose(unittest.TestCase):
    def test_rsi_is_evaluable_again_after_enough_prices_follow_a_halt(self):
        records = series("AAA", 61, {30: (0, 0)})
        result = status(records, records[-1]["date"], RSI)
        self.assertNotEqual(result["status"], "unavailable", result["reason"])

    def test_macd_is_evaluable_again_after_enough_prices_follow_a_halt(self):
        records = series("AAA", 81, {30: (0, 0)})
        result = status(records, records[-1]["date"], MACD)
        self.assertNotEqual(result["status"], "unavailable", result["reason"])

    def test_still_unavailable_while_rewarming_after_the_gap(self):
        records = series("AAA", 36, {30: (0, 0)})
        result = status(records, records[-1]["date"], RSI)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn(records[30]["date"], result["reason"])

    def test_value_before_the_gap_ignores_later_rows(self):
        clean = series("AAA", 61)
        gapped = series("AAA", 61, {40: (0, 0)})
        as_of = clean[35]["date"]
        self.assertEqual(status(clean, as_of, RSI)["value"], status(gapped, as_of, RSI)["value"])


class TestBacktestWithMissingCloses(unittest.TestCase):
    def setUp(self):
        self.costs = CostSettings(
            commission_rate=Decimal("0"),
            commission_discount=Decimal("1"),
            minimum_commission=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        self.factors = {
            "short_window": 2,
            "long_window": 5,
            "momentum_window": 3,
            "volatility_window": 3,
            "top_n": 2,
            "momentum_weight": "1",
            "trend_weight": "1",
            "volatility_weight": "1",
        }

    def backtest(self, records, start, end, **extra):
        return run_portfolio_backtest(
            records,
            start,
            end,
            screening_settings=None,
            initial_cash=Decimal("1000000"),
            quantity=10,
            max_positions=2,
            cost_settings=self.costs,
            ranking_method="factors",
            factor_settings={
                k: Decimal(v) if k.endswith("_weight") else v for k, v in self.factors.items()
            },
            **extra,
        )

    def test_unconfirmed_unpriced_row_before_the_period_does_not_fail_the_backtest(self):
        # 有量無價在回測期間之前（暖機資料中），期間內兩檔都有完整行情。
        records = {"AAA": series("AAA", 60, {20: (25, 2)}), "BBB": series("BBB", 60)}
        days = trading_days(60)
        result = self.backtest(records, days[40], days[-1], confirmed_halts={})
        self.assertEqual(result["equity_curve"][-1]["date"], days[-1])

    def test_unconfirmed_unpriced_day_inside_the_period_is_not_tradable_and_reported(self):
        records = {"AAA": series("AAA", 60, {50: (25, 2)}), "BBB": series("BBB", 60)}
        days = trading_days(60)
        result = self.backtest(records, days[40], days[-1], confirmed_halts={})
        self.assertEqual(result["equity_curve"][-1]["date"], days[-1])
        gap_day = next(p for p in result["equity_curve"] if p["date"] == days[50])
        # 兩檔都在候選內且名額足夠，缺價日 AAA 已持有，以前一日收盤估值並標示日期。
        self.assertIn("AAA", gap_day["holdings"])
        self.assertEqual(gap_day["stale_prices"], {"AAA": days[49]})
        event = [e for e in result["market_events"] if e["stock_id"] == "AAA"]
        self.assertEqual([(e["date"], e["market_status"]) for e in event], [(days[50], "NO_PRICE")])
        self.assertIn("不認定為停牌", event[0]["reason"])
        for order in result["skipped_orders"]:
            if order["stock_id"] == "AAA" and order["date"] == days[50]:
                self.assertEqual(order["reason"], "無成交價（原因未確認），當日不成交")

    def test_missing_row_is_unconfirmed_not_tradable_and_reported(self):
        records = {"AAA": series("AAA", 60), "BBB": series("BBB", 60)}
        del records["AAA"][50]
        days = trading_days(60)
        result = self.backtest(records, days[40], days[-1], confirmed_halts={})
        gap_day = next(p for p in result["equity_curve"] if p["date"] == days[50])
        self.assertEqual(gap_day["stale_prices"], {"AAA": days[49]})
        event = [e for e in result["market_events"] if e["stock_id"] == "AAA"]
        self.assertEqual(
            [(e["date"], e["market_status"]) for e in event], [(days[50], "MISSING_ROW")]
        )
        self.assertIn("不認定為停牌", event[0]["reason"])
        self.assertEqual(
            result["unconfirmed_gaps"],
            [{"stock_id": "AAA", "first": days[50], "last": days[50], "days": 1}],
        )

    def test_missing_row_in_warmup_is_not_filled(self):
        # 暖機期間的缺列不補，與研究排名讀到的序列相同。
        records = {"AAA": series("AAA", 60), "BBB": series("BBB", 60)}
        del records["AAA"][20]
        days = trading_days(60)
        result = self.backtest(records, days[40], days[-1], confirmed_halts={})
        self.assertEqual(result["unconfirmed_gaps"], [])
        self.assertEqual(result["market_events"], [])

    def test_many_missing_rows_on_one_day_mean_the_data_is_not_updated(self):
        records = {sid: series(sid, 60) for sid in ("AAA", "BBB", "CCC")}
        days = trading_days(60)
        for sid in ("AAA", "BBB"):
            del records[sid][55:]
        with self.assertRaisesRegex(
            ValueError,
            f"{days[55]} 有 2 檔（共 3 檔）缺少行情列.*未更新完整.*最後一個資料完整的行情日為 {days[54]}",
        ):
            self.backtest(records, days[40], days[-1], confirmed_halts={})

    def test_the_batch_threshold_is_more_than_ten_percent_and_at_least_two(self):
        days = trading_days(60)
        ids = [f"{1000 + i}" for i in range(25)]

        def run(missing):
            records = {sid: series(sid, 60) for sid in ids}
            for sid in ids[:missing]:
                del records[sid][58]
            return self.backtest(records, days[50], days[-1], confirmed_halts={})

        # 25 檔中 2 檔（8%）：個別缺列，照常完成。
        self.assertEqual(len(run(2)["unconfirmed_gaps"]), 2)
        # 3 檔（12%）：判定為未更新完整。
        with self.assertRaisesRegex(ValueError, "未更新完整"):
            run(3)

    def test_a_stock_without_any_rows_was_never_downloaded(self):
        records = {"AAA": series("AAA", 60), "BBB": []}
        days = trading_days(60)
        with self.assertRaisesRegex(ValueError, "BBB 在 .* 以前沒有任何行情列，可能尚未下載"):
            self.backtest(records, days[40], days[-1], confirmed_halts={})

    def test_a_missing_confirmed_halt_day_is_still_a_halt(self):
        # 沒經過 load_records、清單中的停牌日沒有列：仍是已確認停牌，不是缺列。
        records = {"AAA": series("AAA", 60), "BBB": series("BBB", 60)}
        days = trading_days(60)
        del records["AAA"][50]
        halts = {("AAA", days[50]): {"reason": "測試", "source": "https://x", "sources": []}}
        result = self.backtest(records, days[40], days[-1], confirmed_halts=halts)
        self.assertEqual(result["unconfirmed_gaps"], [])
        self.assertEqual([e["market_status"] for e in result["market_events"]], ["CONFIRMED_HALT"])

    def test_confirmed_halt_stays_in_the_window_like_research_ranking(self):
        # 回測與研究排名同一套規則：停牌列不刪除，窗口（5 筆）內有它時因子不計算。
        records = {"AAA": series("AAA", 60, {45: (0, 0)}), "BBB": series("BBB", 60)}
        days = trading_days(60)
        halts = {("AAA", days[45]): {"reason": "合成停牌", "source": "test fixture"}}
        result = self.backtest(records, days[40], days[-1], confirmed_halts=halts)
        self.assertEqual(result["market_events"][0]["market_status"], "CONFIRMED_HALT")
        unavailable = {
            day for day, stocks in self.ranking_errors(result).items() if "AAA" in stocks
        }
        self.assertEqual(unavailable, set(days[45:50]))

    def ranking_errors(self, result):
        return {row["date"]: row["errors"] for row in result["skipped_rebalances"]}

    def test_strategy_rsi_condition_recovers_after_a_confirmed_halt_in_warmup(self):
        records = {"AAA": series("AAA", 80, {10: (0, 0)}), "BBB": series("BBB", 80)}
        days = trading_days(80)
        halts = {("AAA", days[10]): {"reason": "合成停牌", "source": "test fixture"}}
        strategy = {
            "schema_version": 1,
            "id": "rsi_case",
            "name": "RSI",
            "description": "測試",
            "ranking_mode": "factors",
            "factors": self.factors,
            "screening": {
                "short_window": 2,
                "long_window": 5,
                "volume_window": 3,
                "min_volume_ratio": "1",
                "momentum_window": 3,
                "top_n": 2,
            },
            "conditions": [RSI],
        }
        result = self.backtest(
            records, days[60], days[-1], confirmed_halts=halts, strategy=strategy
        )
        last = result["strategy_diagnostics"][-1]["diagnostics"]["AAA"]
        self.assertNotEqual(last["status"], "unavailable", last.get("reason") or last["conditions"])


class TestSingleStockWithMissingCloses(unittest.TestCase):
    def test_windowed_average_skips_only_windows_that_contain_the_gap(self):
        closes = (
            [Decimal(n) for n in (10, 11, 12, 13, 14)]
            + [None]
            + [Decimal(n) for n in (16, 17, 18, 19)]
        )
        result = windowed_moving_average(closes, 3)
        self.assertEqual(result[:2], [None, None])
        self.assertEqual(result[4], Decimal(13))
        self.assertEqual(result[5:8], [None, None, None])
        self.assertEqual(result[8], simple_moving_average(closes[6:9], 3)[-1])
        self.assertEqual(result[9], Decimal(18))

    def test_engine_does_not_trade_on_a_no_price_day_and_says_why(self):
        # 單檔引擎只在訊號隔日開盤成交；隔日無成交價時這筆訊號不成交，也不順延。
        records = series("2330", 6, {2: (25, 2)})
        result = run_backtest(
            records,
            ["NONE", "BUY", "NONE", "NONE", "NONE", "NONE"],
            Decimal("100000"),
            10,
            confirmed_halts={},
        )
        self.assertEqual(result["trades"], [])
        self.assertEqual(
            result["skipped_orders"],
            [
                {
                    "signal_date": records[1]["date"],
                    "date": records[2]["date"],
                    "action": "BUY",
                    "reason": "無成交價（原因未確認），當日不成交",
                }
            ],
        )
        self.assertEqual(result["market_events"][0]["market_status"], "NO_PRICE")

    def test_engine_values_a_held_position_at_the_last_close_on_a_no_price_day(self):
        records = series("2330", 5, {3: (0, 0)})
        result = run_backtest(
            records,
            ["BUY", "NONE", "NONE", "NONE", "NONE"],
            Decimal("100000"),
            10,
            confirmed_halts={},
        )
        gap = result["equity_curve"][3]
        self.assertEqual(gap["stale_price_date"], records[2]["date"])
        self.assertEqual(gap["equity"], gap["cash"] + records[2]["close"] * 10)

    def run_cli(self, module, *arguments):
        with TemporaryDirectory() as folder:
            config = Path(folder) / "config.toml"
            config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
            save_records(
                series("2330", 30, {12: (25, 2)}), db_path=Path(folder) / "data" / "stocks.db"
            )
            days = trading_days(30)
            argv = [
                module.__name__,
                "--config",
                str(config),
                "--stock",
                "2330",
                "--start",
                days[0],
                "--end",
                days[-1],
                *arguments,
            ]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                code = module.main()
            return code, output.getvalue(), days

    def test_analyze_stock_cli_completes_and_blanks_only_the_affected_averages(self):
        code, output, days = self.run_cli(analyze_stock)
        self.assertEqual(code, 0, output)
        gap_line = next(line for line in output.splitlines() if line.startswith(days[12]))
        self.assertEqual(gap_line.split()[1:4], ["--", "--", "--"])
        later = next(line for line in output.splitlines() if line.startswith(days[20]))
        self.assertNotIn("--", later)

    def test_run_backtest_cli_completes_and_lists_the_no_price_day(self):
        code, output, days = self.run_cli(run_backtest_cli)
        self.assertEqual(code, 0, output)
        self.assertIn(f"{days[12]} | 無成交價（原因未確認，不認定為停牌）", output)


class TestWindowGapReasons(unittest.TestCase):
    """窗口型指標的排除原因要寫出缺價日期，以及再幾個行情日可恢復；承諾的天數必須成立。"""

    FACTORS = {"short_window": 2, "long_window": 5, "momentum_window": 3, "volatility_window": 3}
    RULES = {
        "short_window": 2,
        "long_window": 5,
        "volume_window": 3,
        "min_volume_ratio": Decimal("0"),
        "momentum_window": 3,
    }

    def evaluators(self):
        def condition(kind, **params):
            def run(records, as_of):
                result = status(records, as_of, {"type": kind, **params})
                if result["status"] == "unavailable":
                    raise ValueError(result["reason"])

            return run

        return {
            "因子": lambda records, as_of: calculate_factor_row(
                records, as_of, factor_settings=self.FACTORS
            ),
            "規則篩選": lambda records, as_of: evaluate_stock(records, as_of, **self.RULES),
            "突破": condition("breakout", period=5),
            "均線趨勢": condition("trend", short_window=2, long_window=5),
        }

    def test_reason_names_the_gap_and_the_promised_recovery_holds(self):
        records = series("2330", 50, {35: (25, 2)})
        for name, evaluate in self.evaluators().items():
            with self.subTest(name):
                as_of = records[37]["date"]
                with self.assertRaises(ValueError) as caught:
                    evaluate(records, as_of)
                reason = str(caught.exception)
                self.assertIn(records[35]["date"], reason)
                waiting = int(re.search(r"再 (\d+) 個行情日", reason)[1])
                with self.assertRaises(ValueError):
                    evaluate(records, records[37 + waiting - 1]["date"])
                evaluate(records, records[37 + waiting]["date"])

    def test_reason_counts_several_gaps_and_uses_the_latest(self):
        dates = trading_days(5)
        values = [Decimal(1), None, Decimal(1), None, Decimal(1)]
        reason = window_gap_reason(dates, values)
        self.assertIn(f"{dates[3]} 收盤價缺失", reason)
        self.assertIn("窗口內共 2 筆缺價", reason)
        self.assertIn("再 4 個行情日", reason)
        self.assertIsNone(window_gap_reason(dates, [Decimal(1)] * 5))

    def test_gap_on_the_analysis_date_needs_a_full_window(self):
        records = series("2330", 20, {19: (0, 0)})
        with self.assertRaisesRegex(ValueError, f"{records[19]['date']} 收盤價缺失.*再 5 個行情日"):
            calculate_factor_row(records, records[19]["date"], factor_settings=self.FACTORS)


if __name__ == "__main__":
    unittest.main()
