"""股票搜尋：排序規則、超過上限時的提示、產業篩選，以及個股搜尋頁與選擇器的結果一致。

名單為合成資料。
"""

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG

from tw_stock_backtest.data.universe import save_synthetic_universe
from tw_stock_backtest.stock_search import (
    CODE_EXACT,
    CODE_PREFIX,
    NAME_CONTAINS,
    NAME_PREFIX,
    industries,
    search,
    summary_text,
)
from tw_stock_backtest.workbench import Workbench, create_app


def stock(stock_id, name, industry="半導體業"):
    return {"stock_id": stock_id, "name": name, "industry": industry}


STOCKS = [
    stock("2330", "台積電"),
    stock("2303", "聯電"),
    stock("2317", "鴻海", "其他電子業"),
    stock("1101", "台泥", "水泥工業"),
    stock("2323", "中環", "光電業"),
    stock("5347", "世界", "半導體業"),
    stock("2337", "旺宏"),
    stock("6770", "力積電"),
    stock("3711", "日月光投控"),
    stock("1256", "鮮活果汁-KY", "食品工業"),
    stock("2302", "麗正"),
]


def codes(result):
    return [s["stock_id"] for s in result["stocks"]]


class RankingTests(unittest.TestCase):
    def test_four_tiers_in_order(self):
        # 「23」：代號開頭符合；名稱都不含 23。
        self.assertEqual(
            codes(search(STOCKS, "23")), ["2302", "2303", "2317", "2323", "2330", "2337"]
        )
        # 「2330」：完全符合排第一，只有一筆。
        result = search(STOCKS, "2330")
        self.assertEqual(codes(result), ["2330"])
        self.assertEqual(result["stocks"][0]["match"], CODE_EXACT)

    def test_name_prefix_before_name_contains(self):
        names = STOCKS + [stock("9999", "積電示範")]
        result = search(names, "積電")
        self.assertEqual(codes(result), ["9999", "2330", "6770"])
        self.assertEqual(
            [s["match"] for s in result["stocks"]], [NAME_PREFIX, NAME_CONTAINS, NAME_CONTAINS]
        )

    def test_code_exact_then_prefix_then_name(self):
        mixed = [stock("1234", "甲"), stock("1230", "1234概念"), stock("5678", "丙1234")]
        result = search(mixed, "1234")
        self.assertEqual(codes(result), ["1234", "1230", "5678"])
        self.assertEqual(
            [s["match"] for s in result["stocks"]], [CODE_EXACT, NAME_PREFIX, NAME_CONTAINS]
        )
        prefix = search([stock("1230", "x"), stock("1231", "y"), stock("2123", "z")], "123")
        self.assertEqual(codes(prefix), ["1230", "1231"])  # 代號中間的片段不算
        self.assertEqual({s["match"] for s in prefix["stocks"]}, {CODE_PREFIX})

    def test_full_width_and_case_are_normalized(self):
        self.assertEqual(codes(search(STOCKS, "２３３０")), ["2330"])
        self.assertEqual(codes(search(STOCKS, "ky")), ["1256"])
        self.assertEqual(codes(search(STOCKS, "  台積電 ")), ["2330"])

    def test_no_query_and_no_industry_lists_nothing(self):
        result = search(STOCKS, "")
        self.assertEqual((result["stocks"], result["total"], result["active"]), ([], 0, False))
        self.assertEqual(summary_text(result), "")

    def test_unlisted_four_digit_code(self):
        self.assertEqual(search(STOCKS, "9876")["unlisted_code"], "9876")
        self.assertIsNone(search(STOCKS, "2330")["unlisted_code"])
        self.assertIsNone(search(STOCKS, "23")["unlisted_code"])


class LimitTests(unittest.TestCase):
    def test_more_matches_than_the_limit_are_reported(self):
        many = [stock(f"{2300 + i}", f"股票{i}") for i in range(50)]
        result = search(many, "23", limit=8)
        self.assertEqual(
            (result["total"], len(result["stocks"]), result["truncated"]), (50, 8, True)
        )
        self.assertEqual(
            summary_text(result), "共 50 筆符合，目前顯示前 8 筆，請輸入更多字縮小範圍"
        )
        within = search(many, "2349", limit=8)
        self.assertEqual(summary_text(within), "共 1 筆符合")
        self.assertEqual(summary_text(search(many, "zz")), "找不到符合的股票")


class IndustryTests(unittest.TestCase):
    def test_industry_lists_every_stock_without_the_limit(self):
        many = [stock(f"{2300 + i}", f"股票{i}") for i in range(40)] + [
            stock("1101", "台泥", "水泥工業")
        ]
        result = search(many, "", industry="半導體業", limit=8)
        self.assertEqual(len(result["stocks"]), 40)
        self.assertFalse(result["truncated"])
        self.assertEqual(codes(result), sorted(codes(result)))

    def test_industry_and_text_together(self):
        result = search(STOCKS, "電", industry="半導體業")
        self.assertEqual(codes(result), ["2303", "2330", "6770"])
        self.assertEqual(codes(search(STOCKS, "鴻海", industry="半導體業")), [])

    def test_industry_list_has_counts(self):
        listed = industries(STOCKS + [{"stock_id": "9998", "name": "無產業"}])
        self.assertEqual(listed[0], {"name": "光電業", "count": 1})
        self.assertIn({"name": "半導體業", "count": 7}, listed)
        self.assertNotIn(None, [i["name"] for i in listed])


class PageAndPickerTests(unittest.TestCase):
    """個股搜尋頁（/stocks）與選擇器（/api/stock-search）用同一個函式，結果相同。"""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        config = root / "config.toml"
        config.write_text(TEST_CONFIG, encoding="utf-8")
        listed = [{**s, "market": "合成示例"} for s in STOCKS if s["stock_id"] != "2302"] + [
            {
                "stock_id": f"{4000 + i}",
                "name": f"合成{i}",
                "market": "合成示例",
                "industry": "其他業",
            }
            for i in range(45)
        ]
        save_synthetic_universe(
            listed, root / "data" / "listed_stocks.json", source_url="synthetic://test"
        )
        self.workbench = Workbench(config, root / "reports", root / "state")
        self.addCleanup(self.workbench.close)
        self.client = create_app(self.workbench).test_client()

    def page_codes(self, query="", industry=""):
        text = self.client.get("/stocks", query_string={"q": query, "industry": industry}).text
        return re.findall(r'<span class="stock-code">(\d{4})</span>', text), text

    def api(self, query="", industry=""):
        return self.client.get(
            "/api/stock-search", query_string={"q": query, "industry": industry}
        ).json

    def test_same_order_on_both(self):
        for query, industry in (
            ("23", ""),
            ("積電", ""),
            ("", "半導體業"),
            ("電", "半導體業"),
            ("40", ""),
        ):
            with self.subTest(query=query, industry=industry):
                page, _ = self.page_codes(query, industry)
                picker = [s["stock_id"] for s in self.api(query, industry)["stocks"]]
                # 頁面一次列 30 筆、選擇器 8 筆；選擇器的結果是頁面結果的開頭。
                self.assertEqual(picker, page[: len(picker)])

    def test_limits_and_messages(self):
        page, text = self.page_codes("4")
        self.assertEqual(len(page), 30)
        self.assertIn("共 45 筆符合，目前顯示前 30 筆，請輸入更多字縮小範圍", text)
        result = self.api("4")
        self.assertEqual((result["total"], len(result["stocks"])), (45, 8))
        self.assertEqual(result["summary"], "共 45 筆符合，目前顯示前 8 筆，請輸入更多字縮小範圍")
        page, text = self.page_codes("", "其他業")
        self.assertEqual(len(page), 45)  # 產業瀏覽列出全部
        self.assertIn("共 45 筆符合（產業：其他業）", text)

    def test_empty_page_does_not_list_the_smallest_codes(self):
        page, text = self.page_codes()
        self.assertEqual(page, [])
        self.assertIn("怎麼找股票", text)
        self.assertIn('href="/stocks?industry=%E5%8D%8A%E5%B0%8E%E9%AB%94%E6%A5%AD"', text)
        self.assertIn("data-recent-list hidden", text)

    def test_watchlist_is_shown_when_empty(self):
        self.workbench_notes = self.client.get("/new/research")  # 建立 csrf
        token = re.search('name="csrf" value="([^"]+)"', self.workbench_notes.text)[1]
        self.client.post("/watchlist/2330", data={"csrf": token, "note": "", "revision": "0"})
        _, text = self.page_codes()
        self.assertIn("<h2>自選股</h2>", text)
        self.assertIn('href="/stocks/2330">2330 台積電</a>', text)

    def test_unlisted_code_still_opens(self):
        _, text = self.page_codes("9876")
        self.assertIn('href="/stocks/9876"', text)
        self.assertIn("名單中沒有這個代號", text)
        self.assertEqual(self.api("9876")["unlisted_code"], "9876")

    def test_stock_options_include_industries(self):
        data = self.client.get("/api/stock-options").json
        self.assertIn({"name": "其他業", "count": 45}, data["industries"])
        # 名單（去掉 2302）45 檔合成股，加上設定檔股票池的 AAA。
        self.assertEqual(len(data["stocks"]), len(STOCKS) - 1 + 45 + 1)

    def test_long_queries_are_rejected(self):
        self.assertEqual(self.client.get("/stocks", query_string={"q": "x" * 41}).status_code, 400)
        self.assertEqual(
            self.client.get("/api/stock-search", query_string={"industry": "x" * 41}).status_code,
            400,
        )

    def test_stock_page_marks_recent_view(self):
        text = self.client.get("/stocks/2330").text
        self.assertIn('data-recent-stock="2330" data-recent-name="台積電"', text)


if __name__ == "__main__":
    unittest.main()
