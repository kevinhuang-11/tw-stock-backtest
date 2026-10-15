# 操作與設計指南

[README](../README.md) · [操作導覽](DEMO.md)

一般網頁操作使用選單與表單。以下 CLI 適合批次工作及追查；`<run_id>`／`<report_dir>` 為指令印出的實際目錄，不是可原樣執行的文字。

## 環境與設定

Python 3.12、WSL2 Ubuntu 24.04；測試／CLI 使用專案 `.venv/bin/python`。安裝見 README。Ubuntu 未提供 venv 時先安裝對應的 `python3.12-venv`。

- `config.toml` 保存股票池、指標、下載、資金與成本設定。資料庫相對路徑以設定檔目錄為基準。
- `strategies/` 為示例策略庫；工作台可建立、複製、修改策略。示例並未證明有效。
- 工作台預設為 `127.0.0.1:9000`，保留 Host 與 POST 驗證；不是公開部署服務。
- 安裝 wheel 後仍需自己的設定與策略目錄；CLI 請明確指定 `--config`，工作台另有 `--strategy-dir`。它們不會被當成私人資料打包進 wheel。

```bash
.venv/bin/python -m tw_stock_backtest.cli.serve_workbench \
  --config config.toml --reports-dir reports --state-dir reports/workbench \
  --strategy-dir strategies --port 9000
```

Ctrl+C 關閉時會等既有工作結束。不要同時啟動兩個使用相同 state 的工作台；不刪鎖檔解決重複啟動。修改程式後需自行重啟服務。

## 真實資料準備

資料不隨 Git 提供。**已有資料就沿用，不為展示重新下載。** 以下是新環境的有限三檔準備，會連外；不是 DEMO 的必需步驟。

```bash
.venv/bin/python -m tw_stock_backtest.cli.update_universe
for stock_id in 2330 2317 2454; do
  .venv/bin/python -m tw_stock_backtest.cli.fetch_stock \
    --stock "$stock_id" --start 2026-07-01 --end 2026-09-29 || break
done
.venv/bin/python -m tw_stock_backtest.cli.inspect_db --stock 2330 --limit 5
```

下載普通股名單不是下載全市場行情。逐檔確認資料範圍；來源無法取得時保留失敗，不補造數值。固定歷史示例的資料可用性仍取決於官方來源。

### 合成示例名單

行情庫旁的 `listed_stocks.json` 有兩種範圍：`current_twse_common_stocks`（由 `update_universe` 下載的官方名單）與 `synthetic_example`（離線示例用的合成名單）。合成名單的來源網址以 `synthetic://` 開頭，每檔只有代號、名稱與產業，不含上市日與 CFI 代碼；範圍與來源不一致的檔案會被拒絕讀取。

名單是合成示例時，所有會連網的操作都停用：

- 工作台的「更新自選股資料」「建立更新工作」與研究工作的連線更新，按鈕停用並顯示原因；直接送出請求也會被後端拒絕。
- CLI 的 `update_supplemental`、`update_watchlist`、`fetch_stock`、`download_universe`，以及未加 `--skip-download` 的 `run_research`，會印出原因並以退出碼 1 結束，不發出任何請求。
- `update_universe` 不會以官方名單覆寫合成名單，需要另外指定 `--output`。

只讀本機資料的操作不受影響，例如 `run_research --skip-download`、回測、篩選與保存快照。

## 研究排名與更新

### 更新入口一覽

| 入口 | 範圍 | 更新內容 |
|---|---|---|
| 個股頁「更新這檔行情」 | 單檔 | 開啟建立研究頁並勾選「先連線更新行情」；送出後增量更新行情，再建立研究 |
| 建立研究頁「先連線更新行情」 | 所選股票（最多 10 檔） | 行情，增量 |
| 自選頁「更新自選股資料」 | 自選股（最多 10 檔） | 行情、歷史營收、法人 |
| 個股頁「營收與法人」分頁「建立更新工作」 | 本檔或自選股 | 營收、法人（不含行情） |
| `run_research --universe data/listed_stocks.json --allow-partial` | 整份上市名單 | 行情增量，接著排名 |
| `download_universe` | 整份名單，可分批 | 指定區間內逐月重抓（不是增量） |
| `fetch_stock` | 單檔，指定區間 | 區間內逐月重抓 |

增量以月為單位：重抓每檔最新紀錄所在的月份及之後的月份，停在上個月底附近的股票約需 2 次請求。請求間隔與重試依 `[download]` 設定（預設間隔 3 秒），整份上市名單約 2,100 次請求、約 2 小時。總覽的「資料狀態」會列出行情最新日、停在較舊日期的股票數，以及名單、營收與法人的更新日期；「落後幾個行情日」是依本機行情庫推算，不是交易日曆。

```bash
# 只讀本機行情；保存完整排名，螢幕只顯示前 N 名
.venv/bin/python -m tw_stock_backtest.cli.run_research \
  --config config.toml --skip-download --as-of 2026-09-29 \
  --stocks 2330 2317 2454 --strategy strategies/baseline.json \
  --output-dir reports/research

# 有限增量更新後排名；空資料初始化最近 90 個日曆日
.venv/bin/python -m tw_stock_backtest.cli.run_research --initial-days 90

# 只有明確允許才排除無法評估者後排名
.venv/bin/python -m tw_stock_backtest.cli.run_research \
  --skip-download --as-of 2026-09-29 --stocks 2330 2317 2454 \
  --allow-partial --output-dir reports/research
```

- 月行情更新重抓每股最新紀錄所在月份及之後月份；明確 `--start` 補抓優先，最新日期存在不代表歷史完整。
- `--resume` 指向原 `progress.json`，其他有效參數須相容；保留原計畫並重試失敗項目。舊 `download_universe --resume` 保持自己的進度格式。
- 明確分析日不自動換日。未指定時，以 Asia/Taipei 當日為上限，選指定股票池最新可用行情日作共同基準；每股仍須通過新鮮度檢查。
- **退出碼 0**：完整成功；**2**：允許部分且仍有有效結果；**1**：失敗。條件未通過與資料無法評估不同；有完整資料但零候選仍可能是成功。
- 保存 `settings.json`、`universe.json`、`summary.json`、完整 `rankings.json/CSV`、進度與排除原因。未完成目錄帶 `.incomplete`，不當成完成報表。

## 策略、公式與回測

策略只包含選股邏輯，不包含股票池、期間、資金或成本。一般使用 `/strategies` 表單；CLI 可列出或查看：

```bash
.venv/bin/python -m tw_stock_backtest.cli.strategy_library list
.venv/bin/python -m tw_stock_backtest.cli.strategy_library show baseline
.venv/bin/python -m tw_stock_backtest.cli.run_portfolio \
  --config config.toml --stocks 2330 2317 2454 \
  --start 2026-09-01 --end 2026-09-29 --strategy strategies/baseline.json \
  --sizing fixed_budget --slippage 0.001 --export --output-dir reports/backtests
```

再以新輸出的目錄執行 `plot_report --report-dir <report_dir>`，或從工作台建立回測，完成後直接開結果。繪圖寫入该報表目錄，勿用舊報表覆蓋展示圖。

```bash
.venv/bin/python -m tw_stock_backtest.cli.compare_strategies \
  --config config.toml \
  --strategies strategies/baseline.json strategies/rsi_range.json strategies/macd_breakout.json \
  --stocks 2330 2317 2454 --start 2026-09-01 --end 2026-09-29 \
  --output-dir reports/comparisons
```

跨期間可用工作台的可增刪期間列；CLI 用 `--periods experiments/periods_demo.json` 取代 `--start/--end`。每期獨立重置資金與持股，允許讀開始日前暖機。比較共用股票池、資金、配置與費稅；不把各期報酬相加，也不把勝出期數當未來獲利機率。研究保留期間依 [experiments](../experiments/README.md) 約定，不擅自查看或調參。

| 指標 | 現有定義 |
|---|---|
| EMA(N) | 前 N 個價格的平均作種子，之後平滑係數 `2/(N+1)`；前 N−1 筆無值 |
| RSI(N) | Wilder 平滑，需 N+1 個價格；全平盤 50、單向漲 100、單向跌 0 |
| MACD(F,S,M) | EMA(F)−EMA(S)，訊號線以最初 M 個有效值平均初始化；需 S+M−1 筆，柱狀不乘 2 |
| 突破(N) | 當日收盤嚴格大於前 N 筆最高價，門檻排除當日 |

遞迴指標（EMA、RSI、MACD）只用截至訊號日、最後一次缺價之後的連續收盤價計算：缺價後重新暖機，暖機完成前回報無法評估並說明缺價日期與尚差的筆數；條件結果的 `input_range` 與 `restarted_after` 記錄實際使用的起點。重新暖機後的數值和「假設沒有缺價」的連續序列不同。窗口型指標（SMA、因子、突破、量比）在缺價仍在窗口內時不計算，缺價離開窗口後恢復；排除原因會寫出最後一筆缺價的日期，以及之後還要幾個行情日才會移出窗口（沒有交易日曆，也不看分析日之後的行情，所以不換算成日曆日期）。兩者都不補值、不刪列。圖表顯示範圍不改變計算範圍。新公式未承諾與所有看盤軟體完全一致。

相對因子先按有效母體評分，再套 AND 條件，不因篩選偷偷重算分數。快照保存策略內容及 SHA-256；優先順序為設定檔 → 策略的選股欄位 → 支援的明確 CLI 覆寫。與策略衝突的回測排名模式會被拒絕。

回測使用訊號後的行情日成交，Decimal 處理金額、費稅及滑價。單檔回測不能證明多股票相對選股能力。價格未調整股利與拆股，結果僅為簡化模擬。

停牌與缺價在回測中的處理：

| 情況 | 處理 |
|---|---|
| 已確認停牌（列在 `CONFIRMED_HALTS`） | 當日不成交、不更新該股訊號；持股沿用最近收盤估值並標示日期 |
| 無成交價：來源有該日，但開高低收全空（例如只有零星零股成交） | 同上，但標示為「無成交價（原因未確認）」，不認定為停牌 |
| 其他股票有行情、這檔卻整列不存在 | 整次回測失敗，訊息列出股票與日期；可能是下載缺漏，請重新下載該月或將這檔移出股票池 |

停牌與無成交價日列在 `run_portfolio`／`run_backtest` 的輸出與工作台的回測報表中。回測期間之前（暖機資料）的缺價不會讓回測失敗，只影響指標能否計算。

## 月營收與法人

```bash
# 滾動歷史營收，每檔一次請求，不是任意月份 API
.venv/bin/python -m tw_stock_backtest.cli.update_supplemental \
  --stocks 2330 2317 2454 --kind revenue --revenue-source history \
  --revenue-start 2025-09 --revenue-end 2026-09

# 串接既有增量行情與補充資料；不排名、不寄信
.venv/bin/python -m tw_stock_backtest.cli.update_watchlist \
  --stocks 2330 2317 2454 --kind all
```

- **OpenAPI**：`/v1/opendata/t187ap05_L`，目前快照，金額千元；原 CLI 預設 `--revenue-source latest`，全市場回應一次共用。
- **IIH**：官方頁面公開請求 `/rwd/zh/IIH/company/financial?code=2330`，取 `chart.revenue`；年月為西元 YYYYMM、原單位元，Decimal 精確換算千元。實測滾動 13 月，不擴張成任意歷史 API。
- 合併／個別口徑與公告時間未完整提供，故 `revenue` 與 `revenue_history` 分開保存。成長率只取同來源回應的精確上月／去年同月；缺月份、零／負分母均不能計算。
- **T86 法人**：單位股。三大法人合計為外陸資（不含外資自營商）＋投信＋自營商自行買賣／避險；外資自營商另列，避免重複計入。不是法人持股比例。
- 首次法人最近 35 日、之後回抓 7 日，單次最多 90 日；營收指定範圍最多 24 月。原 timeout、間隔及有限重試保留。
- 補充資料退出碼：全部成功 0；有資料但缺月／無資料／部分失敗 2；無成功資料 1。`complete` 表示工作結束，不表示來源完整。
- `update_supplemental --resume` 驗證日期、來源、股票池、設定及資料庫；成功項目可接續跳過，失敗／部分可重試。`update_watchlist` 無第二套 resume 格式，可使用其子進度接續補充資料，行情重跑按既有資料規劃。

資料保存在行情庫旁的 `supplemental.sqlite3`，含獨立資料、修訂及更新狀態表；不改原 OHLC 或私人筆記。批次寫入與修訂紀錄在同一交易中。

範例（2026-10-13 取得的資料）：2330／2317／2454 各有 2025-09～2026-09 共 13 個月的營收，可以顯示最近 12 個月；更早的月份不足，所以不推算完整一年的年增率。法人資料各有 2026-09-03～10-06 共 22 日，但行情只到 9/29，20 日窗口只對得上 17 筆，三檔都會顯示為資料不足。你實際取得的範圍取決於來源當時提供的內容，可能與此不同。

## 自選與摘要

- 自選總覽各欄保留自己的資料期間。條件、原報表名次及資料完整性分開；數字排序缺值始終在後。
- 沒有名稱或產業就顯示未提供，不從股票代號推測。單股條件不冒充全市場排名。
- 保存全清單快照不受畫面搜尋／篩選影響，預設在 `reports/workbench/watchlist-reports/`。JSON／CSV／HTML／文字在完成後才發布，不覆寫前次。
- 比較需相同策略雜湊、股票池及指定日期模式；原排名另外要求較早研究日與相同有效母體。前次無法評估不算原本未通過。
- 營收新月份、同月修訂及條件變化分開列示。沒有歷史就顯示首次紀錄，不自動補跑。
- 私人筆記在 `reports/workbench/private/watchlist.sqlite3`，有版本衝突檢查，預設不進 CSV／郵件／快照。
- 歷史報表不拿最新資料冒充當時數值；補充資料另列目前保存版本，抓取時間不當公告時間。

## 郵件預覽與寄送邊界

從報表頁直接開 HTML／純文字預覽，不需任何憑證。CLI 範本：

```bash
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary reports/workbench/watchlist-reports/<run_id>/summary.json \
  --dry-run --output reports/mail-preview.eml
```

將 `<run_id>` 換成實際新快照目錄，输出名須未存在。預覽另存 HTML、文字與 CSV，與實際郵件建立共用函式；不重新計算或讀最新行情。

真實寄送只有 CLI 明確 `--send` 才讀取 `GMAIL_ADDRESS` 與 `GMAIL_APP_PASSWORD`，寄件人／收件人同為自己。憑證由使用者自行在 shell 設定，不寫入設定檔、網頁、README 或 Git；用完 `unset GMAIL_ADDRESS GMAIL_APP_PASSWORD`。工作台沒有自動寄送的入口。

## CI 與研究工作流程

- `.github/workflows/ci.yml`：push／pull request／手動觸發；Python 3.12、安裝套件後從乾淨目錄執行離線完整測試、Matplotlib Agg。
- `.github/workflows/research.yml`：只能在 Actions 頁面手動觸發（workflow_dispatch），可指定 `as_of`、`stocks`、`allow_partial`；沒有排程，也不需要任何 secret 或 repository variable。股票池最多 10 檔、請求間隔至少 3 秒，否則在發出請求前就停止。
- cache 只保存 `data/research-cache.db`，每 run／attempt 有新 key，restore prefix 取先前版本；cache 可失效，空 cache 只有限初始化 90 日。
- artifacts 保存明列研究摘要／排名／診斷，保留 14 天；不包括 SQLite、私人工作台筆記或郵件。資料與產物不 commit 回儲存庫。

## 版本庫內容與驗證

| 納入版本庫 | 只留在本機 |
|---|---|
| 原始碼、測試、設定範例、示例策略、文件、報表例圖 | 行情庫、私人自選與筆記、研究報表與郵件預覽、`.env`、快取及 build 產物 |
| `examples/` 的合成資料生成程式 | 生成於 `reports/` 的展示環境 |

只留在本機的項目都已列在 `.gitignore`。

驗證指令：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
```

自動化測試涵蓋到 HTTP 層（以 Flask 測試用戶端請求頁面）。瀏覽器端的互動由 `tools/check_stock_picker.py`（Playwright）檢查，需安裝開發依賴後手動執行，不在 CI 中。
