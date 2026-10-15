"""顯示用格式化：只改畫面文字，缺值不當成零。"""

import unittest
from decimal import Decimal

from tw_stock_backtest.web_format import (
    MISSING,
    fmt_datetime,
    fmt_int,
    fmt_number,
    fmt_percent,
    fmt_signed,
    kind_label,
    status_kind,
    status_label,
    trend,
)


class WebFormatTests(unittest.TestCase):
    def test_numbers_use_separators_and_fixed_digits(self):
        self.assertEqual(fmt_number("66.66666666666666666666666667"), "66.67")
        self.assertEqual(fmt_number(Decimal("1234567.891")), "1,234,567.89")
        self.assertEqual(fmt_number("50.0"), "50.00")
        self.assertEqual(fmt_number("0.125", 2), "0.13")
        self.assertEqual(fmt_number("-0.001"), "0.00")
        self.assertEqual(fmt_int("1107563.5"), "1,107,564")
        self.assertEqual(fmt_int(100), "100")

    def test_ratios_become_percentages(self):
        self.assertEqual(fmt_percent("0.00115"), "0.12%")
        self.assertEqual(fmt_percent("0.009009009009009009"), "0.90%")
        self.assertEqual(fmt_percent("0.001425", 4), "0.1425%")
        self.assertEqual(fmt_percent("0.12", signed=True), "+12.00%")
        self.assertEqual(fmt_percent("-0.0567", signed=True), "−5.67%")
        self.assertEqual(fmt_percent("0", signed=True), "0.00%")
        self.assertEqual(fmt_percent("-0.00001", signed=True), "0.00%")
        self.assertEqual(fmt_signed("-3"), "−3.00")
        self.assertEqual(fmt_signed("2.5", 1), "+2.5")

    def test_missing_or_invalid_values_are_not_shown_as_zero(self):
        for value in (None, "", "None", "--", "abc", float("nan"), Decimal("Infinity"), True):
            with self.subTest(value=value):
                self.assertEqual(fmt_number(value), MISSING)
                self.assertEqual(fmt_percent(value), MISSING)
                self.assertEqual(fmt_signed(value), MISSING)
                self.assertEqual(trend(value), "flat")

    def test_trend_follows_taiwan_convention_names(self):
        self.assertEqual(trend("0.01"), "up")
        self.assertEqual(trend(Decimal("-0.01")), "down")
        self.assertEqual(trend(0), "flat")

    def test_datetime_is_shown_in_taipei_time(self):
        self.assertEqual(
            fmt_datetime("2026-10-14T02:41:38.905766+00:00"),
            "2026-10-14 10:41",
        )
        self.assertEqual(fmt_datetime("2026-10-14T10:41:38+08:00"), "2026-10-14 10:41")
        self.assertEqual(fmt_datetime("2026-09-29"), "2026-09-29 00:00")
        self.assertEqual(fmt_datetime("not a date"), "not a date")
        self.assertEqual(fmt_datetime(None), MISSING)

    def test_status_and_kind_labels(self):
        self.assertEqual(status_label("success"), "完成")
        self.assertEqual(status_kind("success"), "ok")
        self.assertEqual(status_label("unavailable"), "無法評估")
        self.assertEqual(status_kind("failed"), "err")
        # 未知代碼原樣顯示，不猜測含意。
        self.assertEqual(status_label("something_new"), "something_new")
        self.assertEqual(status_kind("something_new"), "neutral")
        self.assertEqual(status_label(None), MISSING)
        self.assertEqual(kind_label("period_comparison"), "跨期間比較")
        self.assertEqual(kind_label("mystery"), "mystery")

    def test_success_never_uses_the_falling_colour(self):
        # 下跌是綠色；成功與通過不能用 down 樣式，避免混淆。
        from tw_stock_backtest.web_format import STATUS

        self.assertNotIn("down", {kind for _, kind in STATUS.values()})
        self.assertNotIn("up", {kind for _, kind in STATUS.values()})


if __name__ == "__main__":
    unittest.main()
