"""缺價對指標與回測的影響；全部使用合成資料。

兩種模式：
- 已確認停牌：開高低收皆空，量、值、筆數為 0，且列在停牌事件中。
- 有量無價：來源有該日，但開高低收為空（例如只有零星零股成交），未確認為停牌。
"""
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
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
    return {"stock_id": stock_id, "date": day, "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 1000 + index,
            "turnover": 100000, "trade_count": 50}


def unpriced(stock_id, day, volume=0, trade_count=0):
    return {"stock_id": stock_id, "date": day, "open": None, "high": None,
            "low": None, "close": None, "volume": volume, "turnover": volume * 100,
            "trade_count": trade_count}


def series(stock_id, count, gaps=None):
    """gaps: {位置: 缺價列的量與筆數}；其餘位置為正常行情。"""
    gaps = gaps or {}
    return [unpriced(stock_id, day, *gaps[i]) if i in gaps else priced(stock_id, day, i)
            for i, day in enumerate(trading_days(count))]


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
        self.costs = CostSettings(commission_rate=Decimal("0"), commission_discount=Decimal("1"),
                                  minimum_commission=Decimal("0"), sell_tax_rate=Decimal("0"))
        self.factors = {"short_window": 2, "long_window": 5, "momentum_window": 3,
                        "volatility_window": 3, "top_n": 2, "momentum_weight": "1",
                        "trend_weight": "1", "volatility_weight": "1"}

    def backtest(self, records, start, end, **extra):
        return run_portfolio_backtest(
            records, start, end, screening_settings=None, initial_cash=Decimal("1000000"),
            quantity=10, max_positions=2, cost_settings=self.costs, ranking_method="factors",
            factor_settings={k: Decimal(v) if k.endswith("_weight") else v
                             for k, v in self.factors.items()}, **extra)

    def test_strategy_rsi_condition_recovers_after_a_confirmed_halt_in_warmup(self):
        records = {"AAA": series("AAA", 80, {10: (0, 0)}), "BBB": series("BBB", 80)}
        days = trading_days(80)
        halts = {("AAA", days[10]): {"reason": "合成停牌", "source": "test fixture"}}
        strategy = {"schema_version": 1, "id": "rsi_case", "name": "RSI", "description": "測試",
                    "ranking_mode": "factors", "factors": self.factors,
                    "screening": {"short_window": 2, "long_window": 5, "volume_window": 3,
                                  "min_volume_ratio": "1", "momentum_window": 3, "top_n": 2},
                    "conditions": [RSI]}
        result = self.backtest(records, days[60], days[-1], confirmed_halts=halts, strategy=strategy)
        last = result["strategy_diagnostics"][-1]["diagnostics"]["AAA"]
        self.assertNotEqual(last["status"], "unavailable", last.get("reason") or last["conditions"])


if __name__ == "__main__":
    unittest.main()
