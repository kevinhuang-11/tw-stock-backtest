"""已確認停牌清單：從套件資料檔讀取並驗證；測試以臨時檔案檢查格式錯誤。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.data.market_events import CONFIRMED_HALTS, load_confirmed_halts

SOURCE = "https://www.twse.com.tw/example"


def entry(**changes):
    return {"stock_id": "1234", "start": "2026-03-02", "end": "2026-03-04",
            "reason": "測試停牌", "sources": [SOURCE], **changes}


class ConfirmedHaltFileTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "halts.json"

    def load(self, *entries, schema_version=1):
        self.path.write_text(json.dumps({"schema_version": schema_version, "halts": list(entries)}),
                             encoding="utf-8")
        return load_confirmed_halts(self.path)

    def test_packaged_file_is_loaded_and_keeps_the_original_event(self):
        event = CONFIRMED_HALTS[("2317", "2025-07-30")]
        self.assertEqual(event["reason"], "重大訊息待公布，暫停交易")
        self.assertTrue(event["source"].startswith("https://investoredu.twse.com.tw/"))
        self.assertEqual(load_confirmed_halts(), CONFIRMED_HALTS)
        for event in CONFIRMED_HALTS.values():
            self.assertTrue(event["sources"] and all(url.startswith("https://") for url in event["sources"]))

    def test_range_is_expanded_to_every_calendar_day(self):
        halts = self.load(entry(start="2026-03-06", end="2026-03-09"))
        self.assertEqual(sorted(day for _, day in halts),
                         ["2026-03-06", "2026-03-07", "2026-03-08", "2026-03-09"])
        self.assertEqual(halts[("1234", "2026-03-07")],
                         {"reason": "測試停牌", "source": SOURCE, "sources": [SOURCE]})

    def test_invalid_entries_are_rejected_instead_of_skipped(self):
        cases = {
            "沒有來源": entry(sources=[]),
            "非 https 來源": entry(sources=["http://www.twse.com.tw/x"]),
            "代號格式": entry(stock_id="12345"),
            "空白原因": entry(reason=" "),
            "起訖顛倒": entry(start="2026-03-05", end="2026-03-04"),
            "日期格式": entry(start="2026/03/02"),
            "區間過長": entry(start="2025-01-01", end="2026-12-31"),
        }
        for name, bad in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.load(bad)

    def test_overlapping_ranges_and_unknown_schema_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "重疊"):
            self.load(entry(), entry(start="2026-03-04", end="2026-03-05"))
        with self.assertRaises(ValueError):
            self.load(entry(), schema_version=2)


if __name__ == "__main__":
    unittest.main()
