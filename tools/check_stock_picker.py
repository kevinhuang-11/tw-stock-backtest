"""在真實瀏覽器中檢查股票選擇器的行為（開發用，需要 Playwright）。

    .venv/bin/python tools/check_stock_picker.py

CI 不執行 JavaScript，所以選擇器的互動由這支腳本驗證：打字建議、鍵盤操作、
貼上多個代號、上限、加入自選、手機觸控目標，以及沒有 JavaScript 時的退路。
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


def main():
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="picker-check-") as temporary:
        root = Path(temporary) / "demo"
        subprocess.run([sys.executable, str(shots.ROOT / "examples" / "prepare_demo.py"),
                        "--output", str(root)], check=True, stdout=subprocess.DEVNULL)
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
                expect(picker.locator(".sp-tag-text").first.inner_text(),
                       "9001 示範股 A（合成）", "標籤顯示代號與名稱")
                picker.get_by_role("button", name="清除全部").click()
                expect(value(), "", "清除全部")

                box.click()
                expect(picker.locator(".sp-option").count(), 3, "聚焦時顯示建議")
                box.type("示範")
                box.press("ArrowDown")
                box.press("Enter")
                expect(value(), "9002", "以名稱搜尋，上下鍵選擇後 Enter 加入")
                box.type("9003")
                box.press("Enter")
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

                page.goto(base + "/")
                page.wait_for_load_state("networkidle")
                with page.expect_request(lambda request: request.method == "POST") as posted:
                    page.get_by_role("button", name="排入研究佇列").click()
                sent = re.search(r'name="stocks"\r\n\r\n([^\r]*)', posted.value.post_data)[1]
                expect(sent, "9001 9002 9003", "送出時仍是 stocks 欄位與空白分隔的代號")
                expect(errors, [], "沒有 JavaScript 錯誤")

                mobile = browser.new_context(
                    locale="zh-TW", viewport={"width": 390, "height": 844},
                    has_touch=True, is_mobile=True).new_page()
                mobile.goto(base + "/dev/components")
                mobile.wait_for_load_state("networkidle")
                small = mobile.locator(".stock-picker").first
                small.get_by_role("button", name="清除全部").tap()
                small.locator(".sp-input").tap()
                heights = mobile.evaluate(
                    "[...document.querySelectorAll('.sp-option')].map(e => e.getBoundingClientRect().height)")
                expect(all(height >= 44 for height in heights) and len(heights) == 3, True,
                       "手機上的建議項目高度至少 44px")
                small.locator(".sp-option").first.tap()
                expect(small.locator("input[name=stocks]").input_value(), "9001", "手機點一下加入")

                plain = browser.new_context(java_script_enabled=False).new_page()
                plain.goto(base + "/")
                field = plain.locator("input[name=stocks]").first
                expect(field.is_visible(), True, "沒有 JavaScript 時是可輸入的文字欄位")
                field.fill("9001 9002")
                plain.get_by_role("button", name="排入研究佇列").click()
                expect(plain.url, base + "/", "沒有 JavaScript 時仍可送出")
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=30)
    print("全部通過")


if __name__ == "__main__":
    main()
