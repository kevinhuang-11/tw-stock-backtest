"""截至分析日的還原價；公司行動與行情都是合成資料，但比例取自 2026 年的官方案例。"""

import unittest
from decimal import Decimal as D

from tw_stock_backtest.analysis.adjustment import AdjustedHistory, adjust, events
from tw_stock_backtest.analysis.factors import calculate_factor_row
from tw_stock_backtest.data.corporate_actions import CAPITAL_REDUCTION, DIVIDEND, PAR_CHANGE

DAYS = [f"2026-03-{d:02d}" for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]


def row(day, close, volume=1000):
    return {
        "stock_id": "1234",
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "turnover": 100,
        "trade_count": 1,
    }


def action(day, kind, previous_close, reference_price, **values):
    base = {
        "stock_id": "1234",
        "effective_date": day,
        "kind": kind,
        "previous_close": D(previous_close),
        "reference_price": D(reference_price),
        "cash_dividend": D(0),
        "stock_dividend_per_1000": D(0),
        "shares_per_1000": None,
        "cash_return": D(0),
    }
    return {**base, **{k: D(v) if isinstance(v, str) else v for k, v in values.items()}}


# 1441 大東：8.72 → 10.13，每千股換發 861 股；恢復日收盤 10.15。
REDUCTION = action(DAYS[5], CAPITAL_REDUCTION, "8.72", "10.13", shares_per_1000="861")
# 2330 除息：1845 → 1838.99，每股 6.000035 元。
DIVIDEND_EVENT = action(DAYS[2], DIVIDEND, "1845", "1838.99", cash_dividend="6.000035")


class AdjustTests(unittest.TestCase):
    def series(self):
        prices = [
            "8.70",
            "8.72",
            "8.72",
            "8.72",
            "8.72",
            "10.15",
            "10.20",
            "10.10",
            "10.30",
            "10.25",
        ]
        return [row(day, D(p), 1000) for day, p in zip(DAYS, prices, strict=True)]

    def test_rows_before_the_event_are_scaled_and_the_gap_disappears(self):
        adjusted = adjust(self.series(), [REDUCTION])
        factor = D("10.13") / D("8.72")
        self.assertEqual(adjusted[4]["close"], D("8.72") * factor)
        self.assertEqual(adjusted[5]["close"], D("10.15"))  # 生效日當天起是原始價格
        # 跳空消失：恢復日相對前一日的報酬約等於 10.15 ÷ 10.13 − 1。
        self.assertAlmostEqual(
            float(adjusted[5]["close"] / adjusted[4]["close"] - 1), 0.00197, places=4
        )
        # 股數比 0.861：舊股成交量換算成新股。
        self.assertEqual(adjusted[0]["volume"], 861)
        self.assertEqual(adjusted[5]["volume"], 1000)

    def test_events_after_the_analysis_date_are_not_used(self):
        raw = self.series()
        before = adjust(raw, [REDUCTION], as_of=DAYS[4])
        self.assertEqual(before, [dict(r) for r in raw[:5]])
        self.assertEqual(adjust(raw, [REDUCTION], as_of=DAYS[5])[4]["close"], D("10.13"))

    def test_missing_prices_stay_missing_and_input_is_not_modified(self):
        raw = self.series()
        raw[3] = {**raw[3], "open": None, "high": None, "low": None, "close": None, "volume": 0}
        snapshot = [dict(r) for r in raw]
        adjusted = adjust(raw, [REDUCTION])
        self.assertIsNone(adjusted[3]["close"])
        self.assertEqual(adjusted[3]["volume"], 0)
        self.assertEqual(raw, snapshot)

    def test_no_actions_returns_copies_of_raw_rows(self):
        raw = self.series()
        self.assertEqual(adjust(raw, []), raw)
        self.assertIsNot(adjust(raw, [])[0], raw[0])

    def test_dividend_keeps_volume_and_stock_dividend_scales_it(self):
        dividend = adjust(self.series(), [DIVIDEND_EVENT])
        self.assertEqual(dividend[0]["volume"], 1000)
        self.assertEqual(dividend[0]["close"], D("8.70") * D("1838.99") / D("1845"))
        stock = action(
            DAYS[2], DIVIDEND, "64", "59.22", cash_dividend="3", stock_dividend_per_1000="30"
        )
        self.assertEqual(adjust(self.series(), [stock])[0]["volume"], 1030)

    def test_par_change_without_known_ratio_adjusts_price_only(self):
        unknown = action(DAYS[5], PAR_CHANGE, "58.80", "29.40")
        adjusted = adjust(self.series(), [unknown])
        self.assertEqual(adjusted[0]["volume"], 1000)
        self.assertEqual(adjusted[0]["close"], D("8.70") / 2)

    def test_same_day_events_multiply(self):
        both = [
            action(DAYS[5], DIVIDEND, "10", "9"),
            action(DAYS[5], CAPITAL_REDUCTION, "9", "18", shares_per_1000="500"),
        ]
        self.assertEqual(events(both), [(DAYS[5], D("1.8"), D("0.5"))])

    def test_ratio_factors_lose_the_artificial_jump(self):
        # 動能（3 日）在恢復日：原始價格看到約 +16%，還原後接近 0。
        settings = {
            "short_window": 2,
            "long_window": 3,
            "momentum_window": 3,
            "volatility_window": 3,
        }
        raw = calculate_factor_row(self.series()[:6], DAYS[5], factor_settings=settings)
        adjusted = calculate_factor_row(
            adjust(self.series()[:6], [REDUCTION]), DAYS[5], factor_settings=settings
        )
        self.assertGreater(raw["momentum"], D("0.16"))
        self.assertLess(abs(adjusted["momentum"]), D("0.01"))


class AdjustedHistoryTests(unittest.TestCase):
    def test_incremental_history_matches_adjust_on_every_day(self):
        raw = AdjustTests().series()
        history = AdjustedHistory([REDUCTION, DIVIDEND_EVENT])
        for index, record in enumerate(raw):
            history.append(record)
            self.assertEqual(
                history.rows,
                adjust(raw[: index + 1], [REDUCTION, DIVIDEND_EVENT], as_of=record["date"]),
                record["date"],
            )

    def test_dates_must_increase(self):
        history = AdjustedHistory().extend([row(DAYS[0], D(1))])
        with self.assertRaises(ValueError):
            history.append(row(DAYS[0], D(1)))


if __name__ == "__main__":
    unittest.main()
