"""共用版型：靜態資產有被打包、圖示都存在、開發用頁面預設不提供。"""
import re
import unittest
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory

from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.workbench import Workbench, create_app

PACKAGE = resources.files("tw_stock_backtest")


class UiBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        config = root / "config.toml"
        config.write_text(TEST_CONFIG.replace("AAA", "2330"), encoding="utf-8")
        self.workbench = Workbench(config, root / "reports", root / "jobs")
        self.addCleanup(self.workbench.close)

    def test_shared_assets_are_packaged_and_served(self):
        # CI 從安裝後的套件執行測試；缺少 package-data 樣式時這裡會失敗。
        for name in ("static/app.css", "static/app.js", "static/icons.svg",
                     "templates/base.html"):
            with self.subTest(name=name):
                self.assertTrue(PACKAGE.joinpath(name).is_file())

        client = create_app(self.workbench).test_client()
        for name, mimetype in (("app.css", "text/css"),
                               ("app.js", "text/javascript"),
                               ("icons.svg", "image/svg+xml")):
            with self.subTest(name=name):
                response = client.get("/static/" + name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, mimetype)
                response.close()

    def test_every_referenced_icon_exists(self):
        sprite = PACKAGE.joinpath("static/icons.svg").read_text(encoding="utf-8")
        defined = set(re.findall(r'<symbol id="([a-z-]+)"', sprite))
        self.assertEqual(len(defined), sprite.count("<symbol "))

        referenced = set()
        for template in PACKAGE.joinpath("templates").iterdir():
            text = template.read_text(encoding="utf-8")
            referenced.update(re.findall(r"icons\.svg#([a-z-]+)", text))
            # 以巨集或變數帶入的圖示名稱：icon('name') 與導覽列的 ('name', '標籤', ...)。
            referenced.update(re.findall(r"icon\('([a-z-]+)'", text))
        # 導覽列的圖示名稱定義在 workbench.py。
        source = PACKAGE.joinpath("workbench.py").read_text(encoding="utf-8")
        navigation = re.findall(r"\('([a-z-]+)', '[^']+', '/", source)
        self.assertEqual(len(navigation), 7)
        referenced.update(navigation)

        self.assertTrue(referenced)
        self.assertEqual(referenced - defined, set())

    def test_no_external_resources_in_templates_or_styles(self):
        # 離線示範不能依賴 CDN：模板、樣式與腳本都不得載入外部網址的資源。
        loaders = re.compile(
            r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//|@import""",
            re.I)
        for folder in ("templates", "static"):
            for item in PACKAGE.joinpath(folder).iterdir():
                text = item.read_text(encoding="utf-8")
                for match in loaders.finditer(text):
                    start = text.rfind("<", 0, match.start())
                    tag = text[start:match.end() + 80]
                    # 一般超連結（<a href>）不是載入資源，允許指向官方資料來源。
                    if folder == "templates" and re.match(r"<a\s", tag):
                        continue
                    self.fail(f"{folder}/{item.name} 載入外部資源：{tag[:120]}")

    def test_dev_page_is_only_registered_in_dev_mode(self):
        normal = create_app(self.workbench).test_client()
        self.assertEqual(normal.get("/dev/components").status_code, 404)

        page = create_app(self.workbench, dev=True).test_client().get("/dev/components")
        self.assertEqual(page.status_code, 200)
        # 已存在的頁面出現在導覽列；每個導覽連結都必須能開啟。
        for label in ("總覽", "自選", "個股", "策略"):
            self.assertIn(label + "</a>", page.text)
        nav = page.text[page.text.index('<nav class="site-nav"'):page.text.index("</nav>")]
        links = re.findall(r'<a href="([^"]+)"', nav)
        self.assertGreaterEqual(len(links), 4)
        for href in links:
            with self.subTest(href=href):
                self.assertNotEqual(normal.get(href).status_code, 404)
        self.assertNotIn("/dev/components", page.text)
        self.assertNotIn("元件展示</a>", page.text)


if __name__ == "__main__":
    unittest.main()
