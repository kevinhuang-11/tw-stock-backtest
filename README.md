# 台股選股與回測工具 v1

以 Python 建立上市普通股的資料下載、候選股排名、歷史評估與回測流程，
作為軟體工程求職作品與學習專案。重點是可追查的資料處理、清楚的交易假設與自動化測試。
目前使用規則與多因子相對排名；ML、券商下單及完整網頁系統不在 v1 範圍內。

快速展示請看 [3～5 分鐘展示指南](docs/DEMO.md)。
後續策略實驗安排沿用 [experiments/README.md](experiments/README.md)，不以展示結果調整基準參數。
「v1」代表本次作品範圍；套件版本目前仍為 `0.1.0`。

## 已完成功能

- 證交所月行情下載、重試、日期篩選與 SQLite 新增／更新；來源缺價保留為 `None`。
- 目前上市普通股名單保存為 JSON；批次下載逐檔保存進度，支援 `--resume`。
- 規則選股：收盤價與均線趨勢、量比篩選，再按動能排序。
- 因子排名：動能、均線趨勢、低波動轉成股票池內相對分數，再依權重加總；沒有最低分數門檻。
- 歷史逐日規則排名、候選股後續價格報酬評估。
- 共用資金的多股票回測，支援固定股數／固定預算、費稅、固定比例滑價及已確認的個別停牌事件。
- 買進持有基準、報酬與最大回撤、持股天數與資金投入比例、JSON／CSV 匯出及資產與回撤圖。

## 架構與資料流

```text
證交所行情／目前上市名單
    → data/：下載、轉換、SQLite 與 JSON 保存
    → analysis/：截至分析日的指標、篩選與排名
    → backtesting/：下一行情日交易、現金與持股、績效與基準
    → reporting.py：保存設定、行情快照與結果
    → plotting.py：讀取報表，產生比較圖
```

| 路徑 | 責任 |
|---|---|
| `src/tw_stock_backtest/cli/` | 命令列參數與流程串接；目前也包含下載流程 |
| `src/tw_stock_backtest/data/` | 資料解析、儲存、名單與市場事件處理 |
| `src/tw_stock_backtest/analysis/` | 指標、規則、因子、歷史排名與後續報酬評估 |
| `src/tw_stock_backtest/backtesting/` | 交易模擬、配置、費稅、基準及績效 |
| `config.toml` | 預設股票池、窗口、權重、資金、成本與資料庫路徑 |
| `tests/` | unittest 單元測試與流程整合測試 |
| `data/`、`reports/` | 本機輸入與輸出，均不納入 Git；新環境需先準備資料 |
| `experiments/` | 基準設定與下一階段的跨期間驗證安排 |
| `docs/DEMO.md` | 展示流程、資料準備與驗證結果 |

## 安裝與環境

開發環境為 WSL2 Ubuntu 24.04、Python 3.12，採 `src` 套件配置。
先切換至實際 clone 的專案根目錄；本機位置是 `/home/user/projects/tw-stock-backtest`。

```bash
cd /home/user/projects/tw-stock-backtest
pwd
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -c 'import sys; print(sys.executable); print(sys.version)'
```

若 Ubuntu 缺少 venv 支援，先安裝系統套件 `python3-venv`。
`pyproject.toml` 要求 Python 3.12 以上，安裝時會帶入 matplotlib。
editable 安裝讓 `src/` 的修改直接生效。已有 `.venv` 時不需重建；以下指令明確指定虛擬環境，不依賴是否執行 `activate`。

## 設定與測試

分析、下載與回測入口可用 `--config` 指定 TOML；命令列支援的覆寫參數優先，且不修改原設定。
資料庫相對路徑以設定檔所在目錄為基準。名單更新使用 `--output`，繪圖使用 `--report-dir`，這兩個入口沒有 `--config`。

```bash
.venv/bin/python -m unittest discover -s tests -v
```

2026-09-30 驗證：252 個測試通過，涵蓋因子窗口、未來資料隔離、成本、停牌、配置、滑價及報表流程。
測試數會隨後續開發改變，應以實際執行為準。

## 資料下載

以下下載命令會連線並寫入本機資料。已有展示資料時，直接跳到排名與回測。
新環境的三檔資料準備見 [展示指南](docs/DEMO.md#新環境沒有-data-時)。

```bash
# 單檔月行情：包含回測開始日前的指標暖機資料
.venv/bin/python -m tw_stock_backtest.cli.fetch_stock --stock 2330 --start 2026-07-01 --end 2026-09-29

# 目前上市普通股名單
.venv/bin/python -m tw_stock_backtest.cli.update_universe --output data/listed_stocks.json

# 小批次示例：名單按代號排序，取前 5 檔；不是完整市場下載
.venv/bin/python -m tw_stock_backtest.cli.download_universe --universe data/listed_stocks.json --start 2026-07-01 --end 2026-09-29 --limit 5

# 將路徑換成上一步輸出的進度檔；其餘參數及設定須與原工作一致
.venv/bin/python -m tw_stock_backtest.cli.download_universe --universe data/listed_stocks.json --start 2026-07-01 --end 2026-09-29 --limit 5 --resume data/download_logs/實際進度檔.json

.venv/bin/python -m tw_stock_backtest.cli.inspect_db --stock 2330 --limit 5
```

`--resume` 跳過已成功或確定略過的股票，重試失敗與尚未完成者。
下載命令成功不保證每個交易日都有行情，仍須檢查日期與缺失資料。

## 選股與歷史評估

下列操作讀取本機資料庫。三檔示例使用 2330、2317、2454；分析日必須有行情。

```bash
.venv/bin/python -m tw_stock_backtest.cli.analyze_stock --stock 2330 --start 2026-08-01 --end 2026-09-29
.venv/bin/python -m tw_stock_backtest.cli.screen_stocks --as-of 2026-09-29 --stocks 2330 2317 2454
.venv/bin/python -m tw_stock_backtest.cli.screen_factors --as-of 2026-09-29 --stocks 2330 2317 2454 --top 3
.venv/bin/python -m tw_stock_backtest.cli.screen_history --start 2026-09-21 --end 2026-09-29 --stocks 2330 2317 2454 --top 3

# 使用已有全名單及行情；接受排除無法評估者後的相對排名
.venv/bin/python -m tw_stock_backtest.cli.screen_factors --as-of 2026-09-29 --universe data/listed_stocks.json --top 20 --allow-partial

# 使用 config.toml 股票池；可將 factors 改為 rules
.venv/bin/python -m tw_stock_backtest.cli.evaluate_selection --start 2026-08-01 --end 2026-09-29 --ranking factors --horizons 5 20 --top 1 2
```

因子只驗證本次所需窗口：20 期動能與 20 期波動需 21 個價格。
窗口外舊缺價不影響計算，窗口內缺價不補零、不前值填補，也不刪列湊窗口。

`evaluate_selection` 觀察下一行情日開盤至第 N 個行情日收盤的價格報酬，進場日算第 1 日。
不計費稅、滑價、股利與拆股；尾端未來行情不足會列為無法評估。
每日樣本可能重疊，平均價格報酬不是累積帳戶報酬。

## 回測、報表與繪圖

```bash
# 單檔均線策略
.venv/bin/python -m tw_stock_backtest.cli.run_backtest --stock 2330 --start 2026-08-01 --end 2026-09-29

# 規則版：預設固定股數
.venv/bin/python -m tw_stock_backtest.cli.run_portfolio --stocks 2330 2317 2454 --start 2026-08-01 --end 2026-09-29 --ranking rules

# 因子版：固定預算、單邊 0.1% 不利滑價，並匯出報表
.venv/bin/python -m tw_stock_backtest.cli.run_portfolio --stocks 2330 2317 2454 --start 2026-08-01 --end 2026-09-29 --ranking factors --sizing fixed_budget --slippage 0.001 --export

# 換成上一步「報表已匯出」的完整目錄
.venv/bin/python -m tw_stock_backtest.cli.plot_report --report-dir reports/實際報表目錄
```

兩種排名共用交易引擎：當日收盤產生名單，下一行情日開盤先賣後買。
固定預算按「初始資金／持股上限」設定單檔上限，包含買進手續費；既有持股不每天重新配置。
基準則將初始資金等分給股票池所有股票，於各檔第一個可交易日買進。
策略與基準採相同費稅及滑價設定，但持股數、投入資金與持有時間可能不同，需同看資金投入比例。
上面兩個策略命令的配置及滑價不同，僅展示功能，不構成控制變因的策略比較。

`--export` 在 `reports/` 建立獨立目錄；可加 `--output-dir /tmp/tw-stock-demo` 指定輸出根目錄。

| 檔案 | 內容 |
|---|---|
| `settings.json` | 實際設定與指定日期 |
| `input_data.json` | 本次輸入行情快照 |
| `results.json` | 策略、基準與績效結果 |
| `trades.csv` | 策略與基準成交紀錄 |
| `equity.csv` | 每日資產比較 |
| `performance.png` | 執行繪圖命令後產生的比較圖 |

## 驗證範圍與限制

- 本機九月行情截至 2026-09-29，不能視為完整九月。該日 1,054 檔名單有 1,026 檔成功評估、28 檔無法評估；此數字只代表當次資料快照。
- 目前上市名單不是歷史完整股票池，存在存活者偏差；小型股票池結果不能推廣至全市場。
- 費稅與滑價是設定模型；尚未處理股利、除權息／拆股調整、精確零股成交、委託簿與成交量限制。
- 已確認停牌由人工事件清單管理；停牌估值可沿用先前收盤價，但不回填原始價格或因子輸入。未知缺價、缺行情不自動視為停牌。
- 回測有獨立的行情完整性檢查，可能拒絕含未知缺價的歷史輸入；單日因子評分成功不保證可直接回測。
- 尚無獨立交易日曆；全部股票共同缺少的日期可能無法發現。
- 期末持股按收盤價估值，不強制賣出；最大回撤以每日收盤資產衡量。
- 程式功能有自動化測試與本機流程驗證；策略尚未完成足夠的跨期間、樣本外驗證，不能據此宣稱有效或可獲利。

下一階段先依既有 experiments 規劃檢查資料與跨期間表現，再決定策略改良；保留區間不在此次文件展示中執行。
