"""停牌期間的賣出意圖（docs/HALT-POLICY.md 3.15、3.23）。全部使用合成資料。

決定賣出、成交日卻不可交易時，保留賣出意圖，在第一個可交易的開盤賣出，
不等恢復後指標重新暖機；單檔引擎的買進訊號遇到不可交易日則作廢。
"""

import unittest
from datetime import date, timedelta
from decimal import Decimal as D

from tw_stock_backtest.backtesting.backtest import run_backtest
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest

COSTS = CostSettings(
    commission_rate=D(0), commission_discount=D(1), minimum_commission=D(0), sell_tax_rate=D(0)
)


def weekdays(count, first=date(2026, 3, 2)):
    days, current = [], first
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


DAYS = weekdays(40)
HALT = DAYS[20:23]
RESUME = DAYS[23]


def row(stock_id, day, price):
    return {
        "stock_id": stock_id,
        "date": day,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1000,
        "turnover": 1,
        "trade_count": 1,
    }


def halted(stock_id, day):
    return {**row(stock_id, day, None), "volume": 0, "turnover": 0, "trade_count": 0}


def records():
    """AAA 先漲後跌、BBB 穩定上漲；AAA 在 HALT 停牌。"""
    result = {"AAA": [], "BBB": []}
    for i, day in enumerate(DAYS):
        aaa = D(100 + i) if i < 18 else D(118 - (i - 18) * 2)
        result["AAA"].append(halted("AAA", day) if day in HALT else row("AAA", day, aaa))
        result["BBB"].append(row("BBB", day, D(100) + D(i) / 2))
    return result


HALTS = {("AAA", day): {"reason": "測試停牌", "source": "https://x", "sources": []} for day in HALT}
FACTORS = {
    "short_window": 2,
    "long_window": 5,
    "momentum_window": 5,
    "volatility_window": 5,
    "top_n": 1,
    "momentum_weight": D(1),
    "trend_weight": D(0),
    "volatility_weight": D(0),
}


class PortfolioTests(unittest.TestCase):
    def run_engine(self):
        return run_portfolio_backtest(
            records(),
            DAYS[10],
            DAYS[-1],
            screening_settings=None,
            ranking_method="factors",
            factor_settings=FACTORS,
            initial_cash=D(100000),
            quantity=100,
            max_positions=1,
            cost_settings=COSTS,
            confirmed_halts=HALTS,
            delistings={},
        )

    def test_sell_decided_before_the_halt_executes_at_the_first_tradable_open(self):
        result = self.run_engine()
        sell = next(t for t in result["trades"] if t["action"] == "SELL")
        # 停牌前一天決定賣出；恢復買賣當天開盤就賣，不等指標窗口清空（改前要再等 6 個行情日）。
        self.assertEqual((sell["stock_id"], sell["date"]), ("AAA", RESUME))
        self.assertEqual(sell["intent_date"], DAYS[19])
        skipped = [s for s in result["skipped_orders"] if s["stock_id"] == "AAA"]
        self.assertEqual(skipped[0]["date"], HALT[0])
        self.assertIn("已確認停牌", skipped[0]["reason"])

    def test_the_halted_holding_keeps_its_slot_until_sold(self):
        result = self.run_engine()
        for point in result["equity_curve"]:
            if HALT[0] <= point["date"] < RESUME:
                self.assertEqual(point["holdings"], {"AAA": 100})
        buy = [t for t in result["trades"] if t["stock_id"] == "BBB"]
        self.assertEqual(buy[0]["date"], RESUME)  # 賣出後同一個開盤才買進


class SingleEngineTests(unittest.TestCase):
    def series(self):
        result = [row("AAA", day, D(100 + i)) for i, day in enumerate(DAYS[:10])]
        result[4] = halted("AAA", DAYS[4])
        result[5] = halted("AAA", DAYS[5])
        return result

    def test_sell_signal_is_kept_until_the_next_tradable_open(self):
        signals = ["BUY", "NONE", "NONE", "SELL", "NONE", "NONE", "NONE", "NONE", "NONE", "NONE"]
        halts = {("AAA", DAYS[i]): {"reason": "測試", "source": "x", "sources": []} for i in (4, 5)}
        result = run_backtest(self.series(), signals, D(100000), 10, confirmed_halts=halts)
        sell = result["trades"][-1]
        self.assertEqual(
            (sell["action"], sell["date"], sell["intent_date"]), ("SELL", DAYS[6], DAYS[3])
        )

    def test_buy_signal_on_an_untradable_day_is_dropped(self):
        signals = ["NONE", "NONE", "NONE", "BUY", "NONE", "NONE", "NONE", "NONE", "NONE", "NONE"]
        halts = {("AAA", DAYS[i]): {"reason": "測試", "source": "x", "sources": []} for i in (4, 5)}
        result = run_backtest(self.series(), signals, D(100000), 10, confirmed_halts=halts)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["skipped_orders"][0]["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
