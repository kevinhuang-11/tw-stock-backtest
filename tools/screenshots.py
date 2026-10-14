"""替工作台的每個頁面截圖（桌面與手機寬度）。

只供本機開發使用，需要先安裝開發依賴與瀏覽器：

    .venv/bin/python -m pip install -e ".[dev]"
    .venv/bin/python -m playwright install chromium

用法：

    .venv/bin/python tools/screenshots.py --out private/ui-after

腳本會在暫存目錄建立離線合成示例、另外跑一次回測與策略比較，啟動工作台後逐頁截圖。
全程不連網，也不讀寫專案的 data/ 與 reports/。
"""
import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZES = {"desktop": (1440, 900), "mobile": (390, 844)}
STATIC_PAGES = {
    "home": "/",
    "strategies": "/strategies",
    "strategy-edit": "/strategies?id=baseline",
    "stock-search": "/stocks",
    "stock-search-query": "/stocks?q=示範",
    "stock": "/stocks/9001",
    "stock-supplemental": "/stocks/9001/supplemental",
    "watchlist": "/watchlist",
    "reports": "/reports",
    "new-research": "/new/research",
    "new-backtest": "/new/backtest",
    "new-backtest-compare": "/new/backtest#compare",
    "new-backtest-periods": "/new/backtest#periods",
}


def run_cli(module, *args):
    """以子程序執行專案 CLI；失敗時直接中止，避免截到不完整的畫面。"""
    env = {**os.environ, "MPLBACKEND": "Agg"}
    subprocess.run(
        [sys.executable, "-m", f"tw_stock_backtest.cli.{module}", *args],
        check=True, env=env, stdout=subprocess.DEVNULL,
    )


def build_demo(root):
    """建立示例，並補上回測、策略比較與第二份研究，讓每種報表頁都有內容。"""
    subprocess.run(
        [sys.executable, str(ROOT / "examples" / "prepare_demo.py"),
         "--output", str(root)],
        check=True, stdout=subprocess.DEVNULL,
    )
    config = ["--config", str(root / "config.toml")]
    strategies = root / "strategies"
    for name in ("rsi_range.json", "macd_breakout.json"):
        (strategies / name).write_bytes((ROOT / "strategies" / name).read_bytes())
    listed = str(root / "data" / "listed_stocks.json")
    period = ["--start", "2026-08-17", "--end", "2026-09-29"]
    baseline = str(strategies / "baseline.json")

    run_cli("run_research", *config, "--skip-download", "--as-of", "2026-09-22",
            "--universe", listed, "--strategy", baseline,
            "--output-dir", str(root / "reports"))
    run_cli("run_portfolio", *config, *period, "--strategy", baseline,
            "--sizing", "fixed_budget", "--export",
            "--output-dir", str(root / "reports"))
    for results in (root / "reports").glob("*/results.json"):
        run_cli("plot_report", "--report-dir", str(results.parent))
    run_cli("compare_strategies", *config, *period, "--strategies", baseline,
            str(strategies / "rsi_range.json"),
            "--output-dir", str(root / "reports"))


def report_pages(root):
    """依報表目錄內容分類；頁面代碼的算法與工作台的報表目錄一致。"""
    pages, research = {}, []
    for folder in sorted(p for base in (root / "reports", root / "workbench")
                         for p in base.rglob("*") if p.is_dir()):
        if folder.name.endswith(".incomplete"):
            continue
        key = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:24]
        if (folder / "comparison.json").is_file():
            pages.setdefault("report-comparison", f"/reports/{key}")
        elif (folder / "results.json").is_file():
            pages.setdefault("report-backtest", f"/reports/{key}")
        elif (folder / "summary.json").is_file():
            summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
            if summary.get("digest_kind") == "watchlist":
                pages.setdefault("report-watchlist", f"/reports/{key}")
                pages.setdefault("preview-watchlist", f"/reports/{key}/preview")
            elif summary.get("report_complete"):
                research.append((summary.get("analysis_date") or "", key))
    research.sort()
    if research:
        latest = research[-1][1]
        pages["report-research"] = f"/reports/{latest}"
        pages["preview-research"] = f"/reports/{latest}/preview"
    if len(research) >= 2:
        pages["compare"] = f"/compare?left={research[0][1]}&right={research[-1][1]}"
    return pages


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_server(root, port, extra):
    command = [
        sys.executable, "-m", "tw_stock_backtest.cli.serve_workbench",
        "--config", str(root / "config.toml"),
        "--reports-dir", str(root / "reports"),
        "--state-dir", str(root / "workbench"),
        "--strategy-dir", str(root / "strategies"),
        "--port", str(port), *extra,
    ]
    server = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/static/app.css", timeout=1)
            return server
        except OSError:
            if server.poll() is not None:
                break
            time.sleep(0.1)
    server.terminate()
    raise RuntimeError("工作台沒有成功啟動")


def submit_research_job(page, base):
    """排入一個離線研究工作，讓工作列表與工作摘要頁有內容。"""
    page.goto(base + "/new/research")
    button = page.get_by_role("button", name="排入研究佇列")
    if button.count() == 0:
        return None
    button.first.click()
    page.wait_for_load_state("networkidle")
    for _ in range(60):
        page.goto(base + "/")
        html = page.content()
        job = re.search(r"/jobs/([0-9a-f]{32})", html)
        if job and not re.search(r"queued|running|排隊中|執行中", html):
            return job[1]
        time.sleep(0.5)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="截圖輸出目錄")
    parser.add_argument("--only", nargs="*", default=None,
                        help="只截名稱包含這些字串的頁面")
    parser.add_argument("--extra-page", action="append", default=[],
                        metavar="NAME=PATH", help="額外頁面，例如 components=/dev/components")
    parser.add_argument("--server-arg", action="append", default=[],
                        help="額外傳給 serve_workbench 的參數")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workbench-shots-") as temporary:
        root = Path(temporary) / "demo"
        build_demo(root)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = start_server(root, port, args.server_arg)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_context(locale="zh-TW").new_page()
                page.set_viewport_size({"width": 1440, "height": 900})
                job = submit_research_job(page, base)

                pages = dict(STATIC_PAGES)
                pages.update(report_pages(root))
                if job:
                    pages["job-summary"] = f"/jobs/{job}/summary"
                    pages["job-mail"] = f"/jobs/{job}/notification.eml"
                for item in args.extra_page:
                    name, _, path = item.partition("=")
                    pages[name] = path
                if args.only is not None:
                    pages = {name: path for name, path in pages.items()
                             if any(part in name for part in args.only)}

                for size, (width, height) in SIZES.items():
                    page.set_viewport_size({"width": width, "height": height})
                    for name, path in pages.items():
                        response = page.goto(base + path)
                        page.wait_for_load_state("networkidle")
                        # 錨點會讓頁面捲到中段；整頁截圖前回到頂端，固定的導覽列才不會出現在中間。
                        page.evaluate("window.scrollTo(0, 0)")
                        page.screenshot(path=str(args.out / f"{name}-{size}.png"),
                                        full_page=True)
                        scroll_width = page.evaluate(
                            "document.documentElement.scrollWidth")
                        note = "  橫向溢出" if scroll_width > width else ""
                        # 只改變 # 的網址在同一頁內切換，不會有新的回應。
                        status = response.status if response else "#"
                        print(f"{size:8} {status} {name}{note}")
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=30)


if __name__ == "__main__":
    main()
