"""股票選擇器的伺服器端部分：資產、退路欄位與自選代號端點。

互動行為需要瀏覽器，由 tools/check_stock_picker.py 驗證。
"""
import re
import unittest
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.stock_research import Watchlist
from tw_stock_backtest.workbench import Workbench, create_app

PACKAGE = resources.files("tw_stock_backtest")


class StockPickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        config = root / "config.toml"
        config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
        self.workbench = Workbench(config, root / "reports", root / "jobs")
        self.addCleanup(self.workbench.close)
        self.client = create_app(self.workbench).test_client()
        self.notes = Watchlist(
            self.workbench.state / "private" / "watchlist.sqlite3")

    def test_assets_are_packaged(self):
        for name in ("static/stock-picker.js", "static/stock-picker.css"):
            with self.subTest(name=name):
                self.assertTrue(PACKAGE.joinpath(name).is_file())

    def test_pages_with_stock_fields_load_the_picker_before_forms(self):
        for url in ("/", "/stocks/2330"):
            with self.subTest(url=url):
                text = self.client.get(url).text
                self.assertIn('href="/static/stock-picker.css"', text)
                picker = text.index('src="/static/stock-picker.js"')
                forms = text.index('src="/static/forms.js"')
                self.assertLess(picker, forms)

    def test_server_html_keeps_a_plain_text_field_as_fallback(self):
        # 沒有 JavaScript 時直接用這個欄位輸入或貼上代號；元件只是在其上加強。
        text = self.client.get("/").text
        fields = re.findall(r'<input name="stocks"[^>]*>', text)
        self.assertTrue(fields)
        for field in fields:
            self.assertNotIn('type="hidden"', field)
            self.assertIn('value="2330"', field)
        self.assertNotIn("<select multiple", "".join(fields))

    def test_old_native_multiselect_picker_is_gone(self):
        script = PACKAGE.joinpath("static/forms.js").read_text(encoding="utf-8")
        self.assertNotIn("choices.multiple", script)
        self.assertNotIn("Ctrl", script)
        self.assertIn("window.StockPicker?.mount", script)

    def test_watchlist_options_returns_codes_only(self):
        self.assertEqual(
            self.client.get("/api/watchlist-options").get_json(), {"stocks": []})

        self.notes.save("2454", "PRIVATE_NOTE_ONLY", 0)
        self.notes.save("2330", "", 0)
        self.notes.save("2317", "", 0)
        self.notes.save("2317", "", 1, remove=True)

        response = self.client.get("/api/watchlist-options")
        self.assertEqual(response.get_json(), {"stocks": ["2330", "2454"]})
        self.assertNotIn("PRIVATE_NOTE_ONLY", response.text)


if __name__ == "__main__":
    unittest.main()
