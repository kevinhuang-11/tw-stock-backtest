"""在真實瀏覽器中檢查股票選擇器的行為（開發用，需要 Playwright）。

    .venv/bin/python tools/check_stock_picker.py

CI 不執行 JavaScript，所以選擇器的互動由這支腳本驗證：打字建議、鍵盤操作、
貼上多個代號、上限、加入自選、手機觸控目標，以及沒有 JavaScript 時的退路。
另外以 60 檔的合成名單檢查搜尋：超過顯示上限的提示、依產業瀏覽、最近查看、
沒有輸入時的說明，以及選擇器與個股搜尋頁的結果一致。
全程使用暫存目錄中的離線合成示例，不連網。
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshots as shots  # noqa: E402  共用啟動示例與伺服器的函式

PASTE = """() => {
  const input = document.querySelector('.sp-input');
  const data = new DataTransfer();
  data.setData('text', %r);
  input.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
}"""


def expect(actual, expected, what):
    if actual != expected:
        raise AssertionError(f"{what}：預期 {expected!r}，實際 {actual!r}")
    print("通過", what)


def wait_value(page, expected):
    """Enter 會等搜尋結果回來才加入；等第一個選擇器的欄位值變成 expected。"""
    page.wait_for_function(
        "expected => document.querySelector('.stock-picker input[name=stocks]').value === expected",
        arg=expected,
        timeout=5000,
    )


def larger_universe(root):
    """把示例名單換成 60 檔（9001～9060），分成兩個產業；原本 3 檔的名稱不變。"""
    import json

    from tw_stock_backtest.data.universe import save_synthetic_universe

    path = root / "data" / "listed_stocks.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    stocks = snapshot["stocks"] + [
        {
            "stock_id": f"{9000 + n}",
            "name": f"合成{n}號",
            "market": "合成示例",
            "industry": "合成甲業" if n % 2 else "合成乙業",
        }
        for n in range(len(snapshot["stocks"]) + 1, 61)
    ]
    save_synthetic_universe(stocks, path, source_url=snapshot["source_url"])


def search_checks(browser, base, root):
    page = browser.new_context(locale="zh-TW").new_page()
    page.goto(base + "/stocks/9010")  # 最近查看（瀏覽器端紀錄）
    page.goto(base + "/dev/components")
    page.wait_for_load_state("networkidle")
    picker = page.locator(".stock-picker").first
    box = picker.locator(".sp-input")
    picker.get_by_role("button", name="清除全部").click()
    box.click()
    picker.locator(".sp-group").nth(1).wait_for()
    expect(
        [g.inner_text() for g in picker.locator(".sp-group").all()],
        ["自選股", "最近查看"],
        "沒有輸入時列出自選股與最近查看",
    )
    expect("9010" in picker.locator(".sp-list").inner_text(), True, "最近查看包含剛看過的 9010")

    box.type("90")
    note = picker.locator(".sp-note")
    note.wait_for()
    expect(
        note.inner_text(), "共 60 筆符合，目前顯示前 8 筆，請輸入更多字縮小範圍", "超過上限時說明"
    )
    shown = [o.inner_text().split()[0] for o in picker.locator(".sp-option").all()]
    expect(shown, [f"{9000 + n}" for n in range(1, 9)], "代號開頭符合，依代號排序")
    page_codes = page.evaluate(
        "fetch('/stocks?q=90').then(r => r.text()).then(t => [...t.matchAll(/stock-code\">(\\d{4})</g)].map(m => m[1]))"
    )
    expect(shown, page_codes[: len(shown)], "選擇器與個股搜尋頁的順序相同")

    box.fill("")
    picker.locator(".sp-industry").select_option("合成甲業")
    picker.locator(".sp-note").wait_for()
    count = picker.locator(".sp-option").count()
    expect(
        picker.locator(".sp-note").inner_text(), f"共 {count} 筆符合", "選了產業時列出全部，不截斷"
    )
    expect(count >= 28, True, "產業內的股票全部列出")
    box.type("5")
    page.wait_for_function(
        "() => document.querySelector('.sp-note') && document.querySelector('.sp-note').textContent === '共 10 筆符合'"
    )
    expect(
        all(
            "合成甲業" in o.inner_text() or "已選" in o.inner_text()
            for o in picker.locator(".sp-option").all()
        ),
        True,
        "產業與文字同時篩選",
    )
    picker.locator(".sp-industry").select_option("")

    # 搜尋還沒回來就按 Enter：等結果出來後選第一筆，不會選到舊清單。
    box.fill("")
    box.type("9045")
    box.press("Enter")
    page.wait_for_function(
        "() => document.querySelector('input[name=stocks]').value.includes('9045')"
    )
    expect(
        "9045" in picker.locator("input[name=stocks]").input_value(),
        True,
        "輸入完馬上按 Enter 也加入正確的股票",
    )

    help_page = browser.new_context(locale="zh-TW").new_page()
    help_page.route("**/api/watchlist-options", lambda route: route.fulfill(json={"stocks": []}))
    help_page.goto(base + "/dev/components")
    help_page.wait_for_load_state("networkidle")
    helper = help_page.locator(".stock-picker").first
    helper.get_by_role("button", name="清除全部").click()
    helper.locator(".sp-input").click()
    helper.locator(".sp-empty").wait_for()
    expect(
        "輸入代號" in helper.locator(".sp-empty").inner_text(), True, "沒有自選與最近查看時顯示說明"
    )

    page.goto(base + "/stocks")
    expect(page.locator("[data-recent-list]").is_visible(), True, "個股搜尋頁列出最近查看")
    expect(page.locator("main .stock-card").count(), 0, "沒有輸入時不預先列出代號最小的股票")


def main():
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="picker-check-") as temporary:
        root = Path(temporary) / "demo"
        subprocess.run(
            [
                sys.executable,
                str(shots.ROOT / "examples" / "prepare_demo.py"),
                "--output",
                str(root),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        larger_universe(root)
        port = shots.free_port()
        base = f"http://127.0.0.1:{port}"
        server = shots.start_server(root, port, ["--dev"])
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_context(locale="zh-TW").new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/dev/components")
                page.wait_for_load_state("networkidle")
                picker = page.locator(".stock-picker").first
                box = picker.locator(".sp-input")
                value = lambda: picker.locator("input[name=stocks]").input_value()  # noqa: E731
                message = lambda: picker.locator(".sp-message").inner_text()  # noqa: E731

                expect(value(), "9001 9002 9003", "預設值轉成標籤後欄位值不變")
                expect(
                    picker.locator(".sp-tag-text").first.inner_text(),
                    "9001 示範股 A（合成）",
                    "標籤顯示代號與名稱",
                )
                picker.get_by_role("button", name="清除全部").click()
                expect(value(), "", "清除全部")

                box.click()
                picker.locator(".sp-group").first.wait_for()
                expect(
                    picker.locator(".sp-group").first.inner_text(), "自選股", "沒有輸入時列出自選股"
                )
                expect(picker.locator(".sp-option").count(), 3, "自選股有 3 檔")
                box.type("示範")
                box.press("ArrowDown")
                box.press("Enter")
                wait_value(page, "9002")
                expect(value(), "9002", "以名稱搜尋，上下鍵選擇後 Enter 加入")
                box.type("9003")
                box.press("Enter")
                wait_value(page, "9002 9003")
                expect(value(), "9002 9003", "輸入代號後 Enter 加入")
                box.press("Backspace")
                expect(value(), "9002", "Backspace 刪除最後一個")

                page.evaluate(PASTE % "9001, 9003 abc 12345；9002")
                expect(value(), "9002 9001 9003", "貼上多個代號，重複的略過")
                expect("無法辨識：abc、12345" in message(), True, "無效代號就地提示")
                expect(box.input_value(), "abc 12345", "無效內容留在輸入框供修正")

                box.fill("")
                picker.get_by_role("button", name="清除全部").click()
                picker.get_by_role("button", name="加入全部自選股").click()
                picker.locator(".sp-message").filter(has_text="自選").wait_for()
                expect(value(), "9001 9002 9003", "加入全部自選股")
                page.evaluate(PASTE % "1101 1102 1103 1104 1108 1109 1110 1201 1203")
                expect(picker.locator(".sp-count").inner_text(), "已選 10 / 10", "上限 10 檔")
                expect("未加入：1201、1203" in message(), True, "超過上限時說明哪些沒加入")
                picker.locator(".sp-remove").first.click()
                expect(value().split()[0], "9002", "按 ✕ 移除")

                page.goto(base + "/new/research")
                page.wait_for_load_state("networkidle")
                with page.expect_request(lambda request: request.method == "POST") as posted:
                    page.get_by_role("button", name="排入研究佇列").click()
                sent = re.search(r'name="stocks"\r\n\r\n([^\r]*)', posted.value.post_data)[1]
                expect(sent, "9001 9002 9003", "送出時仍是 stocks 欄位與空白分隔的代號")
                expect(errors, [], "沒有 JavaScript 錯誤")

                mobile = browser.new_context(
                    locale="zh-TW",
                    viewport={"width": 390, "height": 844},
                    has_touch=True,
                    is_mobile=True,
                ).new_page()
                mobile.goto(base + "/dev/components")
                mobile.wait_for_load_state("networkidle")
                small = mobile.locator(".stock-picker").first
                small.get_by_role("button", name="清除全部").tap()
                small.locator(".sp-input").tap()
                small.locator(".sp-option").first.wait_for()
                heights = mobile.evaluate(
                    "[...document.querySelectorAll('.sp-option')].map(e => e.getBoundingClientRect().height)"
                )
                expect(
                    all(height >= 44 for height in heights) and len(heights) == 3,
                    True,
                    "手機上的建議項目高度至少 44px",
                )
                small.locator(".sp-option").first.tap()
                expect(small.locator("input[name=stocks]").input_value(), "9001", "手機點一下加入")

                search_checks(browser, base, root)

                plain = browser.new_context(java_script_enabled=False).new_page()
                plain.goto(base + "/new/research")
                field = plain.locator("input[name=stocks]").first
                expect(field.is_visible(), True, "沒有 JavaScript 時是可輸入的文字欄位")
                field.fill("9001 9002")
                plain.get_by_role("button", name="排入研究佇列").click()
                expect(plain.url, base + "/", "沒有 JavaScript 時仍可送出")
                plain.goto(base + "/stocks?q=90")
                expect(
                    "共 60 筆符合，目前顯示前 30 筆" in plain.locator("main").inner_text(),
                    True,
                    "沒有 JavaScript 時個股搜尋頁照常搜尋並說明筆數",
                )
                plain.goto(base + "/stocks")
                expect(
                    plain.locator("a.chip", has_text="合成甲業").count(),
                    1,
                    "沒有 JavaScript 時可依產業瀏覽",
                )
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=30)
    print("全部通過")


if __name__ == "__main__":
    main()
