"""研究、篩選、自選與個股頁依價格模式計算；合成行情中有一次「彌補虧損」減資（價格約 ×1.8）。"""

import copy
import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.cli.screen_factors import collect_factor_rows
from tw_stock_backtest.data import corporate_actions as ca
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.prices import PriceSource
from tw_stock_backtest.research import evaluate_research
from tw_stock_backtest.stock_research import current_stock
from tw_stock_backtest.strategies import from_settings
from tw_stock_backtest.watchlist_research import observations
from tw_stock_backtest.workbench import Workbench, create_app


def trading_days(count, first=date(2026, 1, 5)):
    days, current = [], first
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


DAYS = trading_days(60)
EVENT = DAYS[50]


def rows(stock_id):
    """平穩小幅波動；EVENT 起價格乘上 1.8（減資換發後的新股價），其他股票不變。"""
    result = []
    for index, day in enumerate(DAYS):
        price = D(10) + D(index % 3) / 10
        if stock_id == "2330" and day >= EVENT:
            price *= D("1.8")
        result.append(
            {
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
        )
    return result


def reduction():
    before = rows("2330")[49]["close"]
    return {
        "stock_id": "2330",
        "effective_date": EVENT,
        "kind": ca.CAPITAL_REDUCTION,
        "previous_close": before,
        "reference_price": before * D("1.8"),
        "cash_dividend": D(0),
        "stock_dividend_per_1000": D(0),
        "shares_per_1000": D("555.555556"),
        "cash_return": D(0),
        "rights_per_1000": D(0),
        "rights_price": D(0),
        "halt_start": None,
        "needs_review": False,
        "review_notes": [],
        "sources": ["https://www.twse.com.tw/example"],
        "raw": {},
    }


class ReductionFixture(unittest.TestCase):
    """兩檔股票，其中 2330 在 EVENT 減資；公司行動資料涵蓋整年。"""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = self.root / "config.toml"
        config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', 'stocks = ["2330", "2317"]'), encoding="utf-8"
        )
        self.config = config
        self.wb = Workbench(config, self.root / "reports", self.root / "state")
        self.addCleanup(self.wb.close)
        self.settings = self.wb.settings
        self.db = self.settings["storage"]["database_path"]
        save_records(rows("2330") + rows("2317"), db_path=self.db)
        ca.save_actions(
            [reduction()],
            self.db,
            fetched_at="t",
            coverage=[(s, "2026-01-01", "2026-12-31") for s in ("2330", "2317")],
        )
        self.as_of = DAYS[52]


class AdjustedResearchTests(ReductionFixture):
    def with_mode(self, mode):
        return {**self.settings, "corporate_actions": {"mode": mode}}

    def momentum(self, mode):
        stocks = [{"stock_id": "2330"}, {"stock_id": "2317"}]
        factor_rows, errors = collect_factor_rows(
            stocks,
            self.as_of,
            db_path=self.db,
            factor_settings=self.settings["factors"],
            prices=PriceSource(self.with_mode(mode)),
        )
        self.assertEqual(errors, [])
        return {r["stock_id"]: r["momentum"] for r in factor_rows}

    def test_factor_momentum_loses_the_artificial_jump_only_in_adjusted_mode(self):
        raw, adjusted = self.momentum("raw"), self.momentum("adjusted")
        self.assertGreater(raw["2330"], D("0.7"))
        self.assertLess(abs(adjusted["2330"]), D("0.05"))
        self.assertEqual(raw["2317"], adjusted["2317"])

    def test_research_records_the_price_mode_and_actions_used(self):
        stocks = [{"stock_id": "2330"}, {"stock_id": "2317"}]
        cutoff = date.fromisoformat(self.as_of)
        raw = evaluate_research(stocks, self.with_mode("raw"), cutoff, as_of=self.as_of)
        adjusted = evaluate_research(stocks, self.with_mode("adjusted"), cutoff, as_of=self.as_of)
        self.assertEqual(
            raw["price_adjustment"], {"mode": "raw", "actions": [], "uncovered": {}, "restarts": []}
        )
        self.assertEqual(
            [(a["stock_id"], a["effective_date"]) for a in adjusted["price_adjustment"]["actions"]],
            [("2330", EVENT)],
        )
        self.assertEqual(raw["rankings"][0]["stock_id"], "2330")  # 假漲幅讓它排第一
        # 顯示用的訊號日收盤仍是原始價格。
        signal = {e["stock_id"]: e["signal"] for e in adjusted["evaluations"]}
        self.assertEqual(signal["2330"]["close"], rows("2330")[52]["close"])

    def test_strategy_breakout_is_not_triggered_by_the_reduction(self):
        strategy = copy.deepcopy(from_settings(self.settings))
        strategy["conditions"] = [{"type": "breakout", "period": 20}]
        stocks = [{"stock_id": "2330"}]
        cutoff = date.fromisoformat(EVENT)
        raw = evaluate_research(
            stocks, self.with_mode("raw"), cutoff, as_of=EVENT, strategy=strategy
        )
        adjusted = evaluate_research(
            stocks, self.with_mode("adjusted"), cutoff, as_of=EVENT, strategy=strategy
        )
        status = lambda result: result["evaluations"][0]["conditions"][0]["status"]  # noqa: E731
        self.assertEqual(status(raw), "passed")
        self.assertEqual(status(adjusted), "filtered")

    def test_uncovered_periods_are_reported(self):
        save_records(rows("1101"), db_path=self.db)
        source = PriceSource(self.with_mode("adjusted"))
        source.load("1101", "1900-01-01", self.as_of)
        self.assertEqual(source.describe()["uncovered"], {"1101": [[DAYS[0], self.as_of]]})

    def test_stock_page_and_watchlist_follow_the_mode(self):
        strategy = copy.deepcopy(from_settings(self.settings))
        strategy["conditions"] = [{"type": "breakout", "period": 20}]
        self.wb.library.save(strategy, replace=True)
        page = current_stock(self.wb, "2330", {"as_of": EVENT, "strategy_id": strategy["id"]})
        self.assertEqual(page["diagnostic"]["conditions"][0]["status"], "filtered")
        self.assertEqual(page["price_adjustment"]["mode"], "adjusted")

        result = observations(self.wb, ["2330"], strategy_id=strategy["id"], as_of=EVENT)
        row = next(r for r in result["rows"] if r["stock_id"] == "2330")
        self.assertEqual(row["diagnostic"]["conditions"][0]["status"], "filtered")
        self.assertEqual(row["close"], str(rows("2330")[50]["close"]))  # 顯示用的原始收盤
        self.assertEqual(result["price_adjustment"]["mode"], "adjusted")


if __name__ == "__main__":
    unittest.main()


class StockChartBasisTests(ReductionFixture):
    def chart(self, **args):
        return current_stock(self.wb, "2330", {"as_of": DAYS[-1], **args})["chart"]

    def test_raw_is_the_default_and_lists_the_event(self):
        chart = self.chart()
        self.assertEqual(chart["price_basis"], "raw")
        self.assertIn("原始價格", chart["price_adjustment"])
        by_date = {r["date"]: r for r in chart["rows"]}
        self.assertEqual(by_date[DAYS[49]]["close"], str(rows("2330")[49]["close"]))
        self.assertEqual([(e["date"], e["label"]) for e in chart["events"]], [(EVENT, "減資")])

    def test_adjusted_chart_uses_one_series_for_prices_and_indicators(self):
        chart = self.chart(prices="adjusted")
        self.assertEqual(chart["price_basis"], "adjusted")
        closes = [D(r["close"]) for r in chart["rows"]]
        by_date = {r["date"]: r for r in chart["rows"]}
        # 生效日前的收盤已還原，跳空消失。
        self.assertLess(
            abs(D(by_date[EVENT]["close"]) / D(by_date[DAYS[49]]["close"]) - 1), D("0.02")
        )
        # 均線由同一份還原收盤計算。
        window = self.settings["factors"]["long_window"]
        index = len(closes) - 1
        expected = sum(closes[index - window + 1 : index + 1], D(0)) / window
        self.assertEqual(
            D(by_date[DAYS[-1]]["sma"]).quantize(D("0.0001")), expected.quantize(D("0.0001"))
        )

    def test_page_offers_the_toggle_and_rejects_unknown_basis(self):
        client = create_app(self.wb).test_client()
        page = client.get(f"/stocks/2330?as_of={DAYS[-1]}&prices=adjusted")
        self.assertEqual(page.status_code, 200)
        self.assertIn('<option value="adjusted" selected>還原價</option>', page.text)
        self.assertIn("期間內的公司行動", page.text)
        self.assertEqual(client.get(f"/stocks/2330?as_of={DAYS[-1]}&prices=split").status_code, 400)
