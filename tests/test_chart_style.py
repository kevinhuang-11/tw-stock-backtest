"""回測圖：有中文字型時用中文標籤，沒有時改用英文，任何情況都不能畫出方框。"""

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from matplotlib import font_manager
from matplotlib.figure import Figure
from test_end_to_end import TEST_CONFIG
from test_strategy_library import records

from tw_stock_backtest import chart_style
from tw_stock_backtest.config import load_config
from tw_stock_backtest.data.database import save_records
from tw_stock_backtest.strategies import from_settings
from tw_stock_backtest.strategy_comparison import compare_strategies

DEJAVU = font_manager.findfont("DejaVu Sans")


def visible_texts(figure):
    """圖上所有會畫出來的文字：標題、座標軸、刻度、圖例與註記。"""
    texts = [text.get_text() for text in figure.findobj(lambda artist: hasattr(artist, "get_text"))]
    return [text for text in texts if text.strip()]


class Captured:
    """攔截 savefig，保留圖的文字內容，同時照常寫出檔案。"""

    def __init__(self):
        self.texts = []
        original = Figure.savefig

        def capture(figure, *args, **kwargs):
            figure.canvas.draw()
            self.texts.append(visible_texts(figure))
            return original(figure, *args, **kwargs)

        self.patcher = patch.object(Figure, "savefig", capture)


class ChartStyleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = self.root / "config.toml"
        config.write_text(
            TEST_CONFIG.replace("AAA", "2330").replace("quantity = 10", "quantity = 1"),
            encoding="utf-8",
        )
        self.settings = load_config(config)
        save_records(records(), db_path=self.settings["storage"]["database_path"])
        self.strategy = from_settings(self.settings)
        chart_style.cjk_font.cache_clear()
        self.addCleanup(chart_style.cjk_font.cache_clear)

    def comparison_texts(self, english):
        other = copy.deepcopy(self.strategy)
        other.update(id="rsi_demo", name="RSI 區間示例")
        captured = Captured()
        with (
            captured.patcher,
            patch.object(chart_style, "cjk_font", return_value=None)
            if english
            else patch.object(chart_style, "cjk_font", wraps=chart_style.cjk_font),
        ):
            output = compare_strategies(
                self.settings,
                [self.strategy, other],
                "2026-08-25",
                "2026-09-09",
                self.root / ("en" if english else "auto"),
            )
        self.assertTrue((output / "comparison.png").is_file())
        return output, captured.texts[-1]

    def test_glyph_check(self):
        self.assertTrue(chart_style.renders(DEJAVU, "Return 10.76%"))
        self.assertFalse(chart_style.renders(DEJAVU, "報酬"))
        self.assertEqual(chart_style.label("基準示例", "baseline", DEJAVU), "baseline")
        self.assertEqual(chart_style.label("Baseline", "baseline", DEJAVU), "Baseline")

    def test_without_a_chinese_font_every_label_is_drawable(self):
        output, texts = self.comparison_texts(english=True)
        joined = "".join(texts)
        self.assertTrue(chart_style.renders(DEJAVU, joined), joined)
        self.assertIn("Strategy comparison: equity and drawdown", texts)
        self.assertIn("Buy and hold", texts)
        # 中文策略名稱畫不出來時改用只含英數的策略 ID。
        self.assertIn("rsi_demo", texts)
        self.assertNotIn("RSI 區間示例", texts)

        child = next(output.rglob("results.json")).parent
        captured = Captured()
        with captured.patcher, patch.object(chart_style, "cjk_font", return_value=None):
            from tw_stock_backtest.plotting import plot_portfolio_report

            plot_portfolio_report(child)
        texts = captured.texts[-1]
        self.assertTrue(chart_style.renders(DEJAVU, "".join(texts)))
        self.assertTrue(
            any(text.startswith("Portfolio backtest vs buy-and-hold benchmark") for text in texts)
        )
        self.assertTrue((child / "performance.png").is_file())

    def test_footer_states_whether_slippage_was_included(self):
        from tw_stock_backtest.plotting import plot_portfolio_report

        output, _ = self.comparison_texts(english=True)
        child = next(output.rglob("results.json")).parent
        document = json.loads((child / "results.json").read_text(encoding="utf-8"))
        for rate, expected in (("0", "No slippage."), ("0.001", "One-way slippage 0.10%.")):
            with self.subTest(rate=rate):
                document["strategy"]["result"]["slippage_rate"] = rate
                (child / "results.json").write_text(json.dumps(document), encoding="utf-8")
                captured = Captured()
                with captured.patcher, patch.object(chart_style, "cjk_font", return_value=None):
                    plot_portfolio_report(child)
                footer = next(
                    text for text in captured.texts[-1] if text.startswith("Daily closing equity")
                )
                self.assertIn(expected, footer)

    def test_detected_chinese_font_draws_every_label(self):
        found = chart_style.cjk_font()
        if found is None:
            self.skipTest("這個環境沒有中文字型；英文退路由上一個測試驗證")
        _, texts = self.comparison_texts(english=False)
        self.assertIn("策略比較：資產與回撤", texts)
        self.assertIn("RSI 區間示例", texts)
        self.assertTrue(chart_style.renders(found[1], "".join(texts)))

    def test_fake_font_without_glyphs_is_rejected(self):
        # 名稱對得上但缺字的字型不能被當成中文字型。
        with patch.object(chart_style, "_font_path", return_value=DEJAVU):
            self.assertIsNone(chart_style.cjk_font())


if __name__ == "__main__":
    unittest.main()
