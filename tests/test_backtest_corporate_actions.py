"""回測的公司行動帳；比例與金額取自 2026 年的官方公告案例，持股為合成。"""

import unittest
from decimal import Decimal as D

from tw_stock_backtest.backtesting.corporate_actions import (
    Schedule,
    apply_due,
    cash_per_share,
    entitlement,
    share_ratio,
)
from tw_stock_backtest.data.corporate_actions import CAPITAL_REDUCTION, DIVIDEND, PAR_CHANGE


def action(stock_id, day, kind, **values):
    base = {
        "stock_id": stock_id,
        "effective_date": day,
        "kind": kind,
        "previous_close": D("10"),
        "reference_price": D("10"),
        "cash_dividend": D(0),
        "stock_dividend_per_1000": D(0),
        "shares_per_1000": None,
        "cash_return": D(0),
        "needs_review": False,
    }
    return {**base, **{k: D(v) if isinstance(v, str) else v for k, v in values.items()}}


TSMC = action("2330", "2026-03-17", DIVIDEND, cash_dividend="6.000035")
HOTAI = action("6592", "2026-06-30", DIVIDEND, cash_dividend="3", stock_dividend_per_1000="30")
TATUNG_LIKE = action("1441", "2026-09-29", CAPITAL_REDUCTION, shares_per_1000="861")
RADIANT = action("6176", "2026-08-24", CAPITAL_REDUCTION, shares_per_1000="750", cash_return="2.5")
RITEK = action(
    "2323",
    "2026-10-12",
    CAPITAL_REDUCTION,
    shares_per_1000="900",
    cash_return="1",
    cash_dividend="0.2",
)
SPLIT = action("8476", "2024-11-11", PAR_CHANGE, shares_per_1000="2000")


class EntitlementTests(unittest.TestCase):
    def test_cash_dividend_is_rounded_down_to_whole_yuan(self):
        result = entitlement(1000, TSMC)
        self.assertEqual(
            (result["shares_after"], result["cash_dividend"], result["cash"]), (1000, 6000, 6000)
        )
        self.assertEqual(entitlement(1, TSMC)["cash"], 6)

    def test_stock_dividend_adds_whole_shares_and_pays_the_fraction_at_par(self):
        self.assertEqual(entitlement(1000, HOTAI)["shares_after"], 1030)
        odd = entitlement(333, HOTAI)  # 333 × 0.03 = 9.99 股
        self.assertEqual(
            (odd["shares_after"], odd["fraction_cash"], odd["cash_dividend"]), (342, 9, 999)
        )
        self.assertEqual(odd["cash"], 1008)

    def test_capital_reductions_exchange_shares_and_refund_cash(self):
        self.assertEqual(entitlement(1000, TATUNG_LIKE)["shares_after"], 861)
        odd = entitlement(1001, TATUNG_LIKE)  # 861.861 股
        self.assertEqual((odd["shares_after"], odd["fraction_cash"]), (861, 8))
        refund = entitlement(1000, RADIANT)
        self.assertEqual((refund["shares_after"], refund["cash_return"]), (750, 2500))
        combined = entitlement(1000, RITEK)
        self.assertEqual(
            (combined["shares_after"], combined["cash_return"], combined["cash_dividend"]),
            (900, 1000, 200),
        )

    def test_par_change_multiplies_shares(self):
        self.assertEqual(entitlement(1000, SPLIT)["shares_after"], 2000)
        unknown = action("8476", "2024-11-11", PAR_CHANGE)
        with self.assertRaisesRegex(ValueError, "換股率無法確定"):
            entitlement(1000, unknown)

    def test_per_share_factors_for_return_calculations(self):
        self.assertEqual((share_ratio(HOTAI), cash_per_share(HOTAI)), (D("1.03"), D("3")))
        self.assertEqual((share_ratio(RITEK), cash_per_share(RITEK)), (D("0.9"), D("1.2")))

    def test_invalid_holdings_are_rejected(self):
        for bad in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                entitlement(bad, TSMC)


class ScheduleTests(unittest.TestCase):
    def test_events_apply_once_in_date_order_and_only_to_holdings(self):
        schedule = Schedule(
            {"2330": [TSMC], "6176": [RADIANT], "1441": [TATUNG_LIKE]}, "2026-01-01"
        )
        holdings = {"2330": 1000, "6176": 2000}
        cash, details = apply_due(schedule, "2026-08-24", holdings)
        self.assertEqual(cash, 6000 + 5000)
        self.assertEqual(holdings, {"2330": 1000, "6176": 1500})
        self.assertEqual([d["stock_id"] for d in details], ["2330", "6176"])
        self.assertEqual(apply_due(schedule, "2026-08-24", holdings), (0, []))
        # 1441 沒有持股：事件被取出但不產生帳目。
        self.assertEqual(apply_due(schedule, "2026-12-31", holdings), (0, []))

    def test_events_on_or_before_the_start_are_ignored(self):
        schedule = Schedule({"2330": [TSMC]}, "2026-03-17")
        self.assertEqual(apply_due(schedule, "2026-12-31", {"2330": 1000}), (0, []))


if __name__ == "__main__":
    unittest.main()
