# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

開發約定（語言、資料保護、實驗紀律、不擅自 commit）見 @AGENTS.md，以下不重複。

## 常用指令

一律使用專案的 `.venv/bin/python`（Python 3.12）。專案未設定 linter／formatter／型別檢查，測試框架是 `unittest`，不是 pytest。

```bash
# 安裝（已有 .venv 不需重建）
python3.12 -m venv .venv && .venv/bin/python -m pip install -e .

# 完整測試（離線，不需網路或憑證）
.venv/bin/python -m unittest discover -s tests -v

# 單一測試檔／單一測試
.venv/bin/python -m unittest discover -s tests -p 'test_portfolio_backtest.py' -v
.venv/bin/python -m unittest discover -s tests -k <測試名稱片段> -v

# 本機工作台（Flask，只監聽 127.0.0.1）
.venv/bin/python -m tw_stock_backtest.cli.serve_workbench --port 9000

# 無網路的合成展示環境（輸出目錄必須尚未存在，細節見 docs/DEMO.md）
.venv/bin/python examples/prepare_demo.py

# 打包驗證
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
```

所有 CLI 都是 `python -m tw_stock_backtest.cli.<name>`（沒有 console script）；各指令的參數與退出碼約定見 docs/OPERATIONS.md。研究類 CLI 的退出碼：0 完整成功、2 允許部分且仍有有效結果、1 失敗。

CI（`.github/workflows/ci.yml`）是 `pip install .` 後**從乾淨目錄**對安裝後的套件跑測試，所以新增的模板／靜態檔必須符合 `pyproject.toml` 的 `package-data` 樣式，測試也不能依賴原始碼樹的相對路徑。

## 架構

資料流：TWSE 行情 → `data/`（解析、SQLite）→ `analysis/` + `strategies.py`（指標、條件、排名）→ `backtesting/`（成交、成本、基準）→ `reporting.py`／`plotting.py`（報表目錄）→ `research_digest.py`／`notifications.py`（HTML、文字、郵件預覽）。

- **分層**：原則上 `cli/` 只做參數解析與流程串接，計算在 `analysis/`、`backtesting/` 等核心模組。目前有例外：`research.py`、`strategies.py`、`reporting.py`、`workbench.py`、`watchlist_research.py` 會反過來匯入 `cli/` 裡的函式（`save_progress`、`fetch_month_with_retry`、`collect_factor_rows`），修改這些函式時要記得核心模組也在使用。工作台（`workbench.py`）不自行計算，而是把表單轉成 CLI 指令，以序列化的 subprocess 工作執行並瀏覽產出的報表目錄，因此網頁與 CLI 的結果來自同一套核心。
- **設定優先順序**：`config.toml`（`config.py` 載入並驗證）→ 策略 JSON 的選股欄位 → 明確的 CLI 覆寫。策略（`strategies/*.json`，由 `strategies.py` 驗證）只含選股邏輯，不含股票池、期間、資金或成本；快照保存策略內容與 SHA-256。
- **兩個 `strategies.py`**：`tw_stock_backtest/strategies.py` 是策略庫與條件評估；`analysis/strategies.py` 是早期的均線交叉訊號。同樣地，`backtesting/backtest.py` 是單檔引擎，`backtesting/portfolio.py` 是多股票引擎（`rules`／`factors` 兩種排名模式）。
- **三個各自獨立的 SQLite**：行情庫 `stock_prices`（`data/database.py`，價格以字串保存 Decimal，缺價為 NULL）；行情庫旁的 `supplemental.sqlite3`（`data/supplemental.py`，營收與法人，依來源分開並保留修訂）；工作台私人筆記 `watchlist.sqlite3`（`stock_research.py`，不進任何匯出）。營收與法人只供研究展示，不送入排名或回測。
- **合成示例名單會停用連網**：行情庫旁的 `listed_stocks.json` 範圍若是 `synthetic_example`（`data/universe.py`，由 `examples/prepare_demo.py` 產生），`ensure_network_allowed` 會讓所有下載與更新入口在發出請求前失敗，工作台按鈕也會停用。新增任何會連網的入口時要呼叫它；合成名單不可帶官方網址或上市欄位。
- **工作台介面**：所有頁面 `extends 'base.html'`，共用 `static/app.css`（設計變數、元件）、`static/icons.svg` 與 `templates/_components.html` 的巨集；不用 CDN、不用前端框架。顯示用的數字與狀態格式化在 `web_format.py`，只改畫面、不改保存或匯出的數值；報表標題在 `web_views.py`，資料新鮮度在 `data_status.py`。台股慣例紅漲綠跌，狀態標籤不用綠色。互動行為 CI 無法測，改 JS 後跑 `tools/check_stock_picker.py`；截圖用 `tools/screenshots.py`（`--real` 只讀真實行情）。
- **金額一律用 `Decimal`**：費稅、滑價與資金計算不使用 float；費率與捨入方式來自 `[costs]` 設定（`backtesting/costs.py` 的 `CostSettings`）。
- **時間隔離**：訊號只用截至分析日的資料，成交在訊號之後的行情日；缺價不刪列、不補零。
- **缺價的處理（研究、回測、個股圖、單檔 CLI 同一套規則）**：共用函式在 `analysis/indicators.py`（`is_valid_price`、`restart_index`、`segmented`、`windowed_moving_average`）與 `data/market_events.py`（`prepare_market_records`）。窗口型指標（SMA、因子、突破、量比）在缺價仍在窗口內時無法評估；遞迴指標（EMA、RSI、MACD）從最後一次缺價的隔天重新暖機。停牌列與無成交價列都保留在序列中，不跳過。來源有該日但開高低收全空的列標為 `NO_PRICE`（無成交價，原因未確認，不認定為停牌）；它和已確認停牌一樣當日不成交、持股沿用最近收盤估值並標示日期。其他股票有行情、這檔卻整列不存在時，回測仍整次失敗（可能是下載缺漏）。已確認停牌清單目前寫在 `market_events.py` 的 `CONFIRMED_HALTS`。
- **報表是不可覆寫的快照**：每次執行寫入新目錄並保存當時的設定、股票池與輸入；未完成時目錄名帶 `.incomplete`，不可當成完成報表讀取。
- **安全邊界**：`security.py` 負責輸出遮蔽與子程序環境清理；工作台有 Host 與 POST 驗證；郵件憑證只有 `notify_report --send` 才讀環境變數。`tests/test_credential_boundaries.py` 會檢查這些邊界。

## 已知限制（修改相關程式前應知道）

價格未調整除權息與拆股；股票池是目前上市名單，存在倖存者偏差；沒有獨立交易日曆。這些是文件中明示的 v1 限制，不要在未被要求時悄悄「修正」或在文件中淡化。
