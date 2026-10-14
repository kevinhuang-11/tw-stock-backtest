"""替工作台的每個頁面截圖（桌面與手機寬度）。

只供本機開發使用，需要先安裝開發依賴與瀏覽器：

    .venv/bin/python -m pip install -e ".[dev]"
    .venv/bin/python -m playwright install chromium

用法：

    .venv/bin/python tools/screenshots.py --out private/ui-after            # 離線合成示例
    .venv/bin/python tools/screenshots.py --out private/ui-after --real     # 本機真實行情

預設在暫存目錄建立離線合成示例。加上 --real 時改用專案的 config.toml 與真實行情庫，
只讀取行情；研究、回測、比較、自選快照與工作狀態都寫在暫存目錄，所以畫面上不會出現
你原本的報表、自選清單或私人筆記，自選股只放 --stocks 指定的股票且筆記留白。
兩種模式都只在本機計算，不連網，也不寫入專案的 data/ 與 reports/。
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
    "stock-search-query": "/stocks?q={query}",
    "stock": "/stocks/{stock}",
    "stock-supplemental": "/stocks/{stock}/supplemental",
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
    env = {"config": root / "config.toml", "reports": root / "reports",
           "state": root / "workbench", "strategies": root / "strategies",
           "stock": "9001", "query": "示範"}
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
    return env



def build_real(root, config, stocks):
    """真實行情：只讀行情庫，其餘產物都寫在 root。"""
    from tw_stock_backtest.config import load_config
    from tw_stock_backtest.data_status import price_summary
    from tw_stock_backtest.stock_research import Watchlist
    from tw_stock_backtest.strategies import StrategyLibrary
    from tw_stock_backtest.watchlist_research import observations, save_snapshot
    from tw_stock_backtest.workbench import Workbench

    settings = load_config(config)
    dates = price_summary(settings["storage"]["database_path"])[0]
    if len(dates) < 45:
        raise RuntimeError("行情庫的資料不足 45 個行情日，無法建立真實資料截圖")
    env = {"config": Path(config).resolve(), "reports": root / "reports",
           "state": root / "workbench", "strategies": ROOT / "strategies",
           "stock": stocks[0], "query": stocks[0][:2]}
    base = ["--config", str(env["config"]), "--stocks", *stocks]
    baseline = str(env["strategies"] / "baseline.json")
    for day in (dates[-6], dates[-1]):
        run_cli("run_research", *base, "--skip-download", "--as-of", day,
                "--strategy", baseline, "--output-dir", str(env["reports"]))
    period = ["--start", dates[-40], "--end", dates[-1]]
    run_cli("run_portfolio", *base, *period, "--strategy", baseline,
            "--sizing", "fixed_budget", "--export", "--output-dir", str(env["reports"]))
    for results in env["reports"].glob("*/results.json"):
        run_cli("plot_report", "--report-dir", str(results.parent))
    run_cli("compare_strategies", *base, *period, "--strategies", baseline,
            str(env["strategies"] / "rsi_range.json"), "--output-dir", str(env["reports"]))

    # 暫存的自選清單：只放指定股票，筆記留白。
    notes = Watchlist(env["state"] / "private" / "watchlist.sqlite3")
    for sid in stocks:
        notes.save(sid, "", 0)
    workbench = Workbench(env["config"], env["reports"], env["state"],
                          strategy_root=env["strategies"])
    try:
        StrategyLibrary(env["strategies"]).load("baseline")
        save_snapshot(observations(workbench, stocks, "baseline"),
                      env["state"] / "watchlist-reports")
    finally:
        workbench.close()
    return env


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


def start_server(env, port, extra):
    if not isinstance(env, dict):
        # 相容舊的呼叫方式：直接傳入示例目錄。
        env = {"config": env / "config.toml", "reports": env / "reports",
               "state": env / "workbench", "strategies": env / "strategies"}
    command = [
        sys.executable, "-m", "tw_stock_backtest.cli.serve_workbench",
        "--config", str(env["config"]),
        "--reports-dir", str(env["reports"]),
        "--state-dir", str(env["state"]),
        "--strategy-dir", str(env["strategies"]),
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
    parser.add_argument("--real", action="store_true",
                        help="改用本機真實行情（只讀）；其餘產物寫在暫存目錄")
    parser.add_argument("--config", default=str(ROOT / "config.toml"),
                        help="--real 時使用的設定檔")
    parser.add_argument("--stocks", nargs="+", default=["2330", "2317", "2454"],
                        help="--real 時的股票池與暫存自選股")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workbench-shots-") as temporary:
        root = Path(temporary) / ("real" if args.real else "demo")
        env = build_real(root, args.config, args.stocks) if args.real else build_demo(root)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = start_server(env, port, args.server_arg)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_context(locale="zh-TW").new_page()
                page.set_viewport_size({"width": 1440, "height": 900})
                job = submit_research_job(page, base)

                pages = {name: path.format(stock=env["stock"], query=env["query"])
                         for name, path in STATIC_PAGES.items()}
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
