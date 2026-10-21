"""模組相依方向：核心模組不可匯入 cli/。

cli/ 只做參數解析與流程串接，可以匯入核心；反過來會讓核心依賴命令列程式，
也容易形成循環匯入。工作台以子程序執行 CLI，用的是模組名稱字串，不是匯入，不受影響。
檢查的是安裝後的套件原始碼（CI 從乾淨目錄執行），以 AST 找出所有 import。
"""

import ast
import unittest
from pathlib import Path

import tw_stock_backtest

PACKAGE = Path(tw_stock_backtest.__file__).resolve().parent


def imported_modules(path):
    """檔案中所有 import 的模組名稱（含函式內的延遲匯入）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            # from tw_stock_backtest import cli
            names += [f"{node.module}.{alias.name}" for alias in node.names]
    return names


class ModuleLayerTests(unittest.TestCase):
    def test_core_modules_do_not_import_cli(self):
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            relative = path.relative_to(PACKAGE)
            if relative.parts[0] == "cli":
                continue
            for name in imported_modules(path):
                if name == "tw_stock_backtest.cli" or name.startswith("tw_stock_backtest.cli."):
                    offenders.append(f"{relative}: {name}")
        self.assertEqual(offenders, [])

    def test_the_check_finds_lazy_and_package_imports(self):
        # 檢查本身要能抓到函式內的匯入與 from 套件 import cli 的寫法。
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.py"
            sample.write_text(
                "def f():\n"
                "    from tw_stock_backtest.cli.download_universe import save_progress\n"
                "from tw_stock_backtest import cli\n",
                encoding="utf-8",
            )
            names = imported_modules(sample)
        self.assertIn("tw_stock_backtest.cli.download_universe", names)
        self.assertIn("tw_stock_backtest.cli", names)


if __name__ == "__main__":
    unittest.main()
