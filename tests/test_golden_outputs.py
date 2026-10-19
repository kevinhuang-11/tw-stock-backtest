"""黃金輸出：拆解長函式前記錄的完整輸出，拆解後必須逐項相同。

涵蓋 run_portfolio_backtest（多種排名與配置模式、停牌、無成交價、缺列、終止上市、
公司行動與放棄認購）、run_portfolio 與 evaluate_selection 的 CLI 輸出。全部使用合成資料。

更新黃金檔（只在確定行為應該改變時）：GOLDEN_UPDATE=1 執行這個測試檔。
"""

import contextlib
import io
import json
import os
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.backtesting.benchmark import run_buy_and_hold
from tw_stock_backtest.backtesting.costs import CostSettings
from tw_stock_backtest.backtesting.portfolio import run_portfolio_backtest
from tw_stock_backtest.cli import evaluate_selection, run_portfolio
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.serialization import json_default
from tw_stock_backtest.strategies import load_strategy

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"
STRATEGIES = Path(__file__).resolve().parents[1] / "strategies"


def weekdays(count, first=date(2026, 3, 2)):
    result, current = [], first
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


DAYS = weekdays(80)
IDS = ("AAA", "BBB", "CCC", "DDD", "EEE")


def price(stock_index, day_index):
    """每檔不同的走勢：趨勢加上週期，確保排名每天變動。"""
    trend = D(stock_index - 2) * D(day_index) / D(10)
    wave = D((day_index * (stock_index + 3)) % 17 - 8) / D(4)
    return (D(50 + stock_index * 7) + trend + wave).quantize(D("0.01"))


def series(stock_id, stock_index, count=80):
    rows = []
    for i, day in enumerate(DAYS[:count]):
        close = price(stock_index, i)
        open_ = (close - D("0.3") + D(i % 3) / D(5)).quantize(D("0.01"))
        rows.append(
            {
                "stock_id": stock_id,
                "date": day,
                "open": open_,
                "high": max(open_, close) + D("0.5"),
                "low": min(open_, close) - D("0.5"),
                "close": close,
                "volume": 1000 + (i * 37 + stock_index * 101) % 900,
                "turnover": 100000,
                "trade_count": 50,
            }
        )
    return rows


def blank(row):
    return {
        **row,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": 0,
        "turnover": 0,
        "trade_count": 0,
    }


HALT_DAYS = DAYS[45:49]
HALTS = {
    ("BBB", day): {"reason": "合成停牌", "source": "https://x", "sources": ["https://x"]}
    for day in HALT_DAYS
}
DELISTINGS = {
    "EEE": {
        "stock_id": "EEE",
        "announced": DAYS[60],
        "effective": DAYS[70],
        "kind": "financial",
        "reason": "合成終止上市",
        "consideration": None,
        "sources": ["https://x"],
    }
}


def market():
    records = {sid: series(sid, i) for i, sid in enumerate(IDS)}
    for i in range(45, 49):
        records["BBB"][i] = blank(records["BBB"][i])
    records["CCC"][52] = {
        **records["CCC"][52],
        "open": None,
        "high": None,
        "low": None,
        "close": None,
    }
    del records["DDD"][55]  # 缺列
    records["EEE"] = records["EEE"][:70]  # 終止上市日起沒有列
    return records


def action(stock_id, day, kind, **values):
    base = {
        "stock_id": stock_id,
        "effective_date": day,
        "kind": kind,
        "previous_close": D("60"),
        "reference_price": D("58"),
        "cash_dividend": D("0"),
        "stock_dividend_per_1000": D("0"),
        "shares_per_1000": None,
        "cash_return": D("0"),
        "rights_per_1000": D("0"),
        "rights_price": D("0"),
        "needs_review": False,
    }
    return {**base, **values}


ACTIONS = {
    "AAA": [action("AAA", DAYS[50], "dividend", cash_dividend=D("2"))],
    "CCC": [
        action(
            "CCC",
            DAYS[58],
            "capital_reduction",
            previous_close=D("70"),
            reference_price=D("90"),
            shares_per_1000=D("800"),
            cash_return=D("1"),
        )
    ],
    "DDD": [
        action(
            "DDD",
            DAYS[62],
            "dividend",
            previous_close=D("80"),
            reference_price=D("75"),
            rights_per_1000=D("100"),
            rights_price=D("40"),
            rights="decline",
        )
    ],
}
COSTS = CostSettings(
    commission_rate=D("0.001425"),
    commission_discount=D("0.6"),
    minimum_commission=D("20"),
    sell_tax_rate=D("0.003"),
)
RULES = {
    "short_window": 3,
    "long_window": 8,
    "volume_window": 5,
    "min_volume_ratio": D("0.5"),
    "momentum_window": 5,
    "top_n": 3,
}
FACTORS = {
    "short_window": 3,
    "long_window": 8,
    "momentum_window": 5,
    "volatility_window": 5,
    "top_n": 3,
    "momentum_weight": D("1"),
    "trend_weight": D("1"),
    "volatility_weight": D("1"),
}


def scenarios():
    common = dict(
        initial_cash=D("1000000"),
        quantity=1000,
        cost_settings=COSTS,
        confirmed_halts=HALTS,
        delistings=DELISTINGS,
    )
    yield (
        "rules_fixed_quantity",
        dict(
            screening_settings=RULES,
            ranking_method="rules",
            max_positions=2,
            slippage_rate=D("0.001"),
            **common,
        ),
    )
    yield (
        "factors_fixed_budget_actions_zero",
        dict(
            screening_settings=None,
            ranking_method="factors",
            factor_settings=FACTORS,
            max_positions=3,
            sizing_mode="fixed_budget",
            corporate_actions=ACTIONS,
            delisted_valuation="zero",
            **common,
        ),
    )
    yield (
        "strategy_rsi_range",
        dict(
            screening_settings=None,
            strategy=load_strategy(STRATEGIES / "rsi_range.json"),
            max_positions=2,
            corporate_actions=ACTIONS,
            **common,
        ),
    )


def canonical(value):
    return json.loads(json.dumps(value, default=json_default, sort_keys=True))


class GoldenCase(unittest.TestCase):
    maxDiff = None

    def check(self, name, value):
        path = GOLDEN / f"{name}.json"
        value = canonical(value)
        if UPDATE:
            GOLDEN.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        self.assertEqual(value, json.loads(path.read_text(encoding="utf-8")), name)


class EngineGoldenTests(GoldenCase):
    def test_portfolio_engine_outputs_are_unchanged(self):
        for name, kwargs in scenarios():
            with self.subTest(name):
                result = run_portfolio_backtest(market(), DAYS[20], DAYS[-1], **kwargs)
                self.assertTrue(result["trades"])
                self.check(f"portfolio_{name}", result)

    def test_benchmark_outputs_are_unchanged(self):
        result = run_buy_and_hold(
            market(),
            DAYS[20],
            DAYS[-1],
            initial_cash=D("1000000"),
            cost_settings=COSTS,
            confirmed_halts=HALTS,
            delistings=DELISTINGS,
            corporate_actions=ACTIONS,
        )
        self.check("benchmark", result)


class CliGoldenTests(GoldenCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(
            TEST_CONFIG.replace('stocks = ["AAA"]', f"stocks = {json.dumps(list(IDS))}")
            .replace("quantity = 10", "quantity = 100")
            .replace('initial_cash = "1000"', 'initial_cash = "1000000"')
            + '\n[corporate_actions]\nmode = "raw"\n',
            encoding="utf-8",
        )
        rows = [r for records in market().values() for r in records]
        save_records(rows, db_path=self.root / "data" / "stocks.db")

    def normalized(self, text):
        return text.replace(str(self.root), "<ROOT>")

    def run_main(self, module, argv):
        output = io.StringIO()
        patches = (
            patch.object(sys, "argv", argv),
            patch("tw_stock_backtest.data.database.CONFIRMED_HALTS", HALTS),
            patch("tw_stock_backtest.backtesting.portfolio.CONFIRMED_HALTS", HALTS),
            patch("tw_stock_backtest.backtesting.benchmark.CONFIRMED_HALTS", HALTS),
            patch("tw_stock_backtest.backtesting.portfolio.CONFIRMED_DELISTINGS", DELISTINGS),
            patch("tw_stock_backtest.backtesting.benchmark.CONFIRMED_DELISTINGS", DELISTINGS),
            patch("tw_stock_backtest.data.market_events.CONFIRMED_DELISTINGS", DELISTINGS),
            patch("tw_stock_backtest.cli.evaluate_selection.CONFIRMED_HALTS", HALTS),
        )
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(output))
            code = module.main()
        return code, self.normalized(output.getvalue())

    def test_run_portfolio_output_is_unchanged(self):
        for name, extra in (
            ("rules", ["--ranking", "rules"]),
            (
                "strategy",
                ["--strategy", str(STRATEGIES / "baseline.json"), "--sizing", "fixed_budget"],
            ),
        ):
            with self.subTest(name):
                reports = self.root / f"reports-{name}"
                code, output = self.run_main(
                    run_portfolio,
                    [
                        "run_portfolio",
                        "--config",
                        str(self.config),
                        "--start",
                        DAYS[20],
                        "--end",
                        DAYS[-1],
                        "--slippage",
                        "0.001",
                        "--show-equity",
                        "--export",
                        "--output-dir",
                        str(reports),
                        *extra,
                    ],
                )
                (folder,) = reports.iterdir()
                results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
                settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
                settings.pop("created_at_utc")
                files = sorted(p.name for p in folder.iterdir())
                csvs = {
                    n: (folder / n).read_text(encoding="utf-8-sig")
                    for n in ("trades.csv", "equity.csv")
                }
                self.check(
                    f"cli_run_portfolio_{name}",
                    {
                        "code": code,
                        "stdout": output.replace(folder.name, "<REPORT>"),
                        "results": results,
                        "settings": json.loads(self.normalized(json.dumps(settings))),
                        "files": files,
                        "csv": csvs,
                    },
                )

    def test_evaluate_selection_output_is_unchanged(self):
        for ranking in ("rules", "factors"):
            with self.subTest(ranking):
                code, output = self.run_main(
                    evaluate_selection,
                    [
                        "evaluate_selection",
                        "--config",
                        str(self.config),
                        "--start",
                        DAYS[20],
                        "--end",
                        DAYS[-1],
                        "--ranking",
                        ranking,
                        "--horizons",
                        "1",
                        "5",
                        "--top",
                        "1",
                        "2",
                    ],
                )
                self.check(f"cli_evaluate_selection_{ranking}", {"code": code, "stdout": output})


if __name__ == "__main__":
    unittest.main()
