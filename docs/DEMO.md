# v1 展示指南（3～5 分鐘）

目的：展示從資料到候選排名、交易模擬、基準比較及可追查報表的工程流程。
使用已查看過的 2026 年 8～9 月案例；不打開 experiments 規劃的保留驗證區間。
所有命令在專案根目錄執行，先完成 README 的環境安裝。

## 展示前準備

- 確認 `.venv/bin/python` 可執行，先跑完整測試，展示時只說明結果。
- 確認 `data/stocks.db` 有 2330、2317、2454 的 2026 年 7～9 月行情，且至少到 9/29；7 月供 8 月初指標暖機。
- 不需全市場名單即可展示三檔排名與回測。現有全名單可作為加分展示，不是必要前置條件。
- 使用根目錄 `config.toml` 的既有參數；下方歷史評估使用其中的三檔股票池。
- 先跑一次匯出與繪圖，把新產生的 `performance.png` 在 IDE 開啟，現場不等待下載。

## 新環境沒有 data/ 時

`data/` 不納入 Git；clone 後沒有資料庫是正常情況。
先在連網環境下載三檔即可，無須下載全市場或先建立股票名單。
這些步驟只在準備階段執行，會建立／更新 `data/stocks.db`。

```bash
for stock_id in 2330 2317 2454; do
  .venv/bin/python -m tw_stock_backtest.cli.fetch_stock --stock "$stock_id" --start 2026-07-01 --end 2026-09-29 || break
done
.venv/bin/python -m tw_stock_backtest.cli.inspect_db --stock 2330 --limit 5
.venv/bin/python -m tw_stock_backtest.cli.screen_factors --as-of 2026-09-29 --stocks 2330 2317 2454 --top 3
```

逐一確認三檔的下載結果與日期。下載失敗時先處理來源／網路問題，不用空值替代價格。
若來源尚未提供這段資料，這組固定日期示例不能直接展示；需另行準備有完整行情與暖機資料的區間，並同步修改全部展示日期。
此次文件驗證未重新下載資料；外部來源可用性未重驗。

## 操作順序

### 0:00～0:40：目的與資料流

開啟 README 架構表，說明「SQLite 保存行情，analysis 產生名單，backtesting 模擬資金，reporting 保存結果」。
強調來源缺價會保留，程式會清楚回報無法評估，不把所有缺價當成停牌。

### 0:40～1:20：候選股排名

```bash
.venv/bin/python -m tw_stock_backtest.cli.screen_factors --as-of 2026-09-29 --stocks 2330 2317 2454 --top 3
```

指出原始因子值、各因子分數與總分；分數是這三檔之間的相對排名，不是獲利機率。
若使用已有全名單，可改跑下方「其他 CLI 操作」的 `--universe ... --allow-partial` 命令，說明成功與失敗數量。
新環境只準備三檔時不要使用全名單模式。

### 1:20～2:40：回測與基準比較

```bash
.venv/bin/python -m tw_stock_backtest.cli.run_portfolio --stocks 2330 2317 2454 --start 2026-08-01 --end 2026-09-29 --ranking factors --sizing fixed_budget --slippage 0.001 --export
```

依序指出：

1. 訊號日期與成交日期不同，使用下一行情日開盤成交。
2. 固定預算、手續費、交易稅與滑價都會影響股數及現金。
3. 同看報酬、最大回撤與平均持股市值占比，不只看哪個報酬高。
4. 買進持有採整個股票池等預算配置，與策略的持股上限不同。

### 2:40～3:30：報表與圖表

複製命令末尾「報表已匯出」的路徑，替換下列佔位文字：

```bash
.venv/bin/python -m tw_stock_backtest.cli.plot_report --report-dir reports/實際報表目錄
```

在 IDE 開啟該目錄的 `performance.png`，展示資產與回撤比較。
再指出 `settings.json`、`input_data.json` 與 `trades.csv`：結果可以回頭查設定、輸入與每筆成交。
繪圖會寫入指定目錄的 `performance.png`；使用本次新匯出的目錄，避免覆蓋既有圖表。
WSL 不必啟動 GUI 視窗，直接用 IDE 檢視 PNG 即可。

### 3:30～4:30：驗證與限制

若時間允許，補充「候選股的後續表現」與「帳戶回測」的差別：

```bash
.venv/bin/python -m tw_stock_backtest.cli.evaluate_selection --start 2026-08-01 --end 2026-09-29 --ranking factors --horizons 5 20 --top 1 2
```

尾端缺少足夠未來行情而無法評估是預期行為；不能補資料或把該筆當零報酬。
這裡的平均價格報酬未扣成本、樣本可能重疊，不能當作帳戶累積報酬。

結尾可說：「目前已驗證資料處理與交易模擬的程式行為。策略是否有效仍需跨期間及樣本外驗證，下一階段會沿用既有實驗計畫。」

## 本次實際驗證（2026-09-30）

- 三檔排名、歷史規則排名、規則回測、因子固定預算與滑價回測、後續報酬評估均成功。
- 因子回測含買進持有比較，成功匯出 JSON／CSV 並產生 PNG；驗證輸出位於 `/tmp`，未改動既有 data/ 與 reports/。
- 完整測試 252 個通過。下載命令只核對程式與 CLI 參數，沒有再次連線下載。
- 正式展示流程沒有阻擋問題；外部資料下載與新機器環境仍需在展示前準備完成。

## 查看回測參數

```bash
.venv/bin/python -m tw_stock_backtest.cli.run_portfolio --help
```

已修正 argparse help 字串的百分比跳脫，命令可正常執行，滑價說明顯示 `0.1%`。

## 其他 CLI 操作

以下為主展示流程以外的操作。下載命令會連線並寫入資料，已有資料時不需重跑。
`download_universe` 的範例只取名單前 5 檔；`--resume` 路徑請換成原工作輸出的進度檔，並保持原日期、股票範圍及設定。
全名單排名需要先有名單及對應行情，三檔資料不足以執行全名單展示。

```bash
.venv/bin/python -m tw_stock_backtest.cli.fetch_stock --stock 2330 --start 2026-07-01 --end 2026-09-29
.venv/bin/python -m tw_stock_backtest.cli.update_universe --output data/listed_stocks.json
.venv/bin/python -m tw_stock_backtest.cli.download_universe --universe data/listed_stocks.json --start 2026-07-01 --end 2026-09-29 --limit 5
.venv/bin/python -m tw_stock_backtest.cli.download_universe --universe data/listed_stocks.json --start 2026-07-01 --end 2026-09-29 --limit 5 --resume data/download_logs/實際進度檔.json
.venv/bin/python -m tw_stock_backtest.cli.analyze_stock --stock 2330 --start 2026-08-01 --end 2026-09-29
.venv/bin/python -m tw_stock_backtest.cli.screen_stocks --as-of 2026-09-29 --stocks 2330 2317 2454
.venv/bin/python -m tw_stock_backtest.cli.screen_history --start 2026-09-21 --end 2026-09-29 --stocks 2330 2317 2454 --top 3
.venv/bin/python -m tw_stock_backtest.cli.screen_factors --as-of 2026-09-29 --universe data/listed_stocks.json --top 20 --allow-partial
.venv/bin/python -m tw_stock_backtest.cli.run_backtest --stock 2330 --start 2026-08-01 --end 2026-09-29
.venv/bin/python -m tw_stock_backtest.cli.run_portfolio --stocks 2330 2317 2454 --start 2026-08-01 --end 2026-09-29 --ranking rules
```

`--resume` 跳過成功與確定略過的股票，重試失敗及尚未完成者。
下載命令成功不保證每日行情完整，仍需檢查日期及缺失資料。
`--config` 可指定 TOML，CLI 支援的覆寫參數優先；資料庫相對路徑以設定檔所在目錄為基準。

## 計算與回測假設

因子只驗證本次所需窗口：20 期動能與波動需要 21 個價格。
窗口外舊缺價不影響結果；窗口內不補零、不以前值填補，也不刪列湊窗口。

兩種排名共用交易引擎：當日收盤產生名單，下一行情日開盤先賣後買。
固定預算按「初始資金／持股上限」設定單檔上限，含買進手續費；既有持股不每天重新配置。
基準將初始資金等分給所有股票，在各檔第一個可交易日買進。
策略與基準使用相同費稅及滑價設定，但持股數、投入資金及持有時間不同，需同看資金投入比例。
本文件規則版命令使用預設配置，因子版覆寫配置與滑價，兩者僅供展示功能，不能直接當成控制變因的比較。

後續報酬評估從下一行情日開盤計至第 N 個行情日收盤，進場日算第 1 日。
未計費稅、滑價、股利與拆股；平均價格報酬不能當作累積帳戶報酬。

## 報表檔案

`--export` 在 reports/ 建立獨立目錄，也可用 `--output-dir /tmp/tw-stock-demo` 指定輸出根目錄。

| 檔案 | 內容 |
|---|---|
| `settings.json` | 實際設定與指定日期 |
| `input_data.json` | 本次輸入行情快照 |
| `results.json` | 策略、基準與績效結果 |
| `trades.csv` | 策略與基準成交紀錄 |
| `equity.csv` | 每日資產比較 |
| `performance.png` | 執行繪圖命令後產生的比較圖 |

## 展示數據與限制

2026-09-29 全名單評分：1,054 檔中 1,026 檔成功、28 檔無法評估。
失敗原因為 24 檔窗口內缺收盤價、2 檔行情未更新、1 檔筆數不足、1 檔沒有分析日以前行情。
資料庫九月行情截至 9/29，不能當作完整九月。

README 圖表採三檔等權因子排名、初始資金 100 萬、持股上限 2、固定預算與單邊 0.1% 滑價。
指定區間 2026-08-01～9/29，實際行情區間 8/3～9/29。
策略報酬／最大回撤為 10.7629%／9.2774%，基準為 10.2557%／7.0224%。
報酬較高的同時回撤也較大，單次結果不能證明策略有效。

目前上市名單不是歷史完整股票池，存在存活者偏差；三檔回測不能推廣至全市場。
尚無獨立交易日曆，所有股票共同缺少的日期可能無法發現。
回測另做行情完整性檢查，單日排名成功不保證整段歷史能回測。
已確認停牌可沿用舊收盤價估值，但不回填原始價格或因子輸入。
尚未處理股利、除權息／拆股調整、精確零股成交及委託簿／成交量限制。
期末持股按收盤價估值，不強制賣出；最大回撤以每日收盤資產衡量。

## 自動化研究流程

### CI 與定時研究的差別

`.github/workflows/ci.yml` 在 push、pull_request、workflow_dispatch 時執行 Python 3.12 測試。
先安裝套件，再從 runner 暫存目錄跑完整 unittest；不使用本機行情、憑證或真實股市 API。
Matplotlib 使用 Agg backend。CI 只有 contents: read 權限、10 分鐘 timeout。

`.github/workflows/research.yml` 才會連線下載行情；採 20 分鐘 timeout，預設使用 config.toml 的三檔股票池。
已完成兩次手動遠端驗收，結果見文末；定時變數仍未啟用。

### CLI 範例

在專案根目錄執行，所有日期為 YYYY-MM-DD。`--as-of` 同時是下載截止日及明確分析日。

```bash
# 不下載，使用設定檔股票池；歷史日期不會被偷偷更換
.venv/bin/python -m tw_stock_backtest.cli.run_research --skip-download --as-of 2026-09-29 --output-dir /tmp/tw-stock-research

# 指定股票；自動增量更新，空資料最多初始化最近 90 個日曆日
.venv/bin/python -m tw_stock_backtest.cli.run_research --stocks 2330 2317 2454 --initial-days 90 --top 3

# 明確補抓範圍，不因資料庫已有較新資料而略過；使用獨立資料庫
.venv/bin/python -m tw_stock_backtest.cli.run_research --stocks 2330 --start 2026-08-01 --as-of 2026-09-29 --database /tmp/tw-stock-example.db --output-dir /tmp/tw-stock-example-reports

# 已有上市名單與本機全市場資料時，離線評分並保存全部成功股票的排名
.venv/bin/python -m tw_stock_backtest.cli.run_research --skip-download --as-of 2026-09-29 --universe data/listed_stocks.json --top 20 --allow-partial --output-dir /tmp/tw-stock-universe-research

# 接續原研究工作：保留原本股票、日期、設定及模式，只加 resume 路徑
.venv/bin/python -m tw_stock_backtest.cli.run_research --stocks 2330 --start 2026-08-01 --as-of 2026-09-29 --database /tmp/tw-stock-example.db --output-dir /tmp/tw-stock-example-reports --resume /tmp/tw-stock-example-reports/原run目錄/progress.json
```

`--stocks` 與 `--universe` 互斥；沒有指定時使用設定檔股票池。
`--limit` 僅搭配上市名單，用於小批次。`--top` 只限制螢幕顯示，JSON／CSV 保存全部實際排名母體。
`--database` 覆寫本次資料庫，不改 config.toml。`--skip-download` 不搭配 `--start` 或 `--resume`。

### 增量規劃與接續

- 有資料：重抓截至截止日的最新紀錄所在月份，及其後月份；藉 SQLite upsert 寫回修訂，不新增重複日期。
- 無資料：以截止日往前 `initial-days`（預設 90 天）作有限初始化；介面請求仍按月份發出，只保存範圍內的列。
- 指定 `--start`：明確起日優先，與 `--as-of` 組成補抓區間；不刪除區間外已有行情。
- 名單有上市日：不抓上市日前資料。上市日晚於截止日標記 skipped，並保留原因。
- 單月成功即寫入資料庫並保存進度。某月失敗會停止該檔後續月份，避免較新日期掩蓋失敗；仍繼續下一檔股票。
- success 為全部請求成功且有保存資料；no_data 為有請求但範圍內無行情；partial 為部分月份無資料；failed 為請求、解析或儲存失敗。
- 無資料不代表停牌；缺價保持 None，評分時沿用既有窗口檢查。
- 來源無資料僅辨識既有明確訊息；未知回應視為失敗，不猜測原因。upsert 不會刪除來源後來撤回的列。

progress.json 保存工作身分雜湊、截止日、股票池、有效設定（含絕對資料庫路徑）、程式 commit 及原月份計畫。
`--resume` 不重新推導起日，跳過已成功股票，重試失敗、無資料、部分完成及中斷項目；已成功股票會檢查本機仍有行情。
接續也建立新 run 目錄，不覆蓋原報表。未指定 as-of 的工作接續時沿用原截止日，隔天也不會變成另一個工作。
股票池、設定、日期或資料庫不同會拒絕接續；進度檔不是資料庫備份，不能只拿進度檔到沒有原資料的 runner 繼續。
舊 `download_universe --resume` 完全保留；兩種進度格式不可混用。

最新日期存在只代表有那筆行情，不能證明更早日期完整；更早月份修訂或缺漏需明確補抓。
曾失敗的歷史補抓應用原 progress 接續，不能期待後續一般增量更新自動修復所有舊缺口。

### 日期、嚴格模式與退出碼

- 時區為 Asia/Taipei；明確 as-of 必須不晚於台北當日，且不會自動換日期。
- 自動模式取指定股票池中，不晚於截止日的最大已保存行情日期，所有股票都用這一個日期評分。
- 每檔仍需有該日行情；不會為了較舊的股票把分析日往回退。當日收盤價缺值、窗口不足或缺值仍拒絕。
- 螢幕與摘要分別顯示採用日期、台北今日及日曆日差；較早結果明示為歷史行情。
- 尚無交易日曆，因此週末或休市日可能採用較早日期。成功表示「該基準日期的流程完整」，不保證已取得今日或全部交易日資料。

| 退出碼 | 狀態 | 排名行為 |
|---|---|---|
| 0 | success | 所有股票通過該基準日評估，更新成功（或已驗證的 resume 略過）；離線模式不要求下載 |
| 2 | partial | 僅 allow-partial 才允許排除股票後產生排名，且至少有一檔可排名 |
| 1 | failed | 嚴格模式有任何排除、沒有可排名股票，或參數／資料庫／報表錯誤；不產生完整排名 |

本版採保守規則：本次更新 failed、partial、no_data 的股票一律排除，即使本機舊資料仍可計算。
下載錯誤不被舊行情掩蓋；若要明確採用現有資料，可另開一次 skip-download 執行。
嚴格模式失敗仍保存評估與排除摘要，rankings JSON／CSV 為空／僅標題。

### 輸出與中斷

預設 `reports/research/<run_id>/`，每次建立不同目錄：

| 檔案 | 內容 |
|---|---|
| settings.json | run_id、參數、有效設定、Git commit 與 dirty 標記、開始時間 |
| universe.json | 實際股票池、來源類型、名單下載時間（若有） |
| progress.json | 固定下載計畫、工作身分、逐檔逐月狀態，供 resume |
| summary.json | 開始／結束時間、指定／實際分析日、逐檔實際日期、評估值、排除與下載原因、母體、各狀態數量、退出碼 |
| rankings.json／rankings.csv | 全部實際排名，Decimal 用精確字串，CSV 支援中文 |

執行中目錄以 `.incomplete` 結尾。JSON 重用既有暫存檔替換方式，全部檔案完成後才原子改名；
消費者只讀非 incomplete 且 summary.report_complete=true 的目錄。這個旗標表示「報表已寫完」，不代表研究成功，仍須看 status／exit_code。
意外錯誤會盡量寫入 failure.json；硬中斷可能只留下 progress.json，可用來接續。
參數驗證未通過或輸出路徑無法建立時可能沒有報表；雲端另保存 runner-diagnostic.txt。
不複製整個資料庫、不保存環境變數全集或原始 HTTP 錯誤內容。無 Git 資訊時 commit／dirty 為 null，仍可執行。

### GitHub 手動與定時設定

完成 review 並由你 push workflow 到預設分支後：

1. GitHub → Actions → Research → Run workflow。
2. as_of 可留空或填歷史截止日；stocks 可留空使用 config，或填 `2330 2317 2454`。
3. allow_partial 預設 false。雲端最多 10 檔，設定的請求間隔不得小於 3 秒。
4. 完成後下載 `research-<run_id>-<attempt>` artifact；部分成功會顯示 warning，退出碼 1 會讓 job 失敗，兩者均先嘗試上傳診斷。

研究排程為 UTC 平日 08:23，即台灣平日 16:23。只有 repository variable
`RESEARCH_SCHEDULE_ENABLED` 精確等於字串 `true` 才執行定時 job；未設定、刪除或設為 `false` 都停用。
設定位置：Settings → Secrets and variables → Actions → Variables。手動觸發不受此變數限制。
不需要額外 Secrets、API Token、Gmail 或其他通知憑證。
排程可能延遲或被平台略過，不等於交易日曆；workflow 需存在於預設分支才有排程／手動入口。
同分支研究工作使用 concurrency 串行，新的觸發不取消正在執行的工作；GitHub 仍可能合併待執行工作，不能當作可靠每日佇列。

### 雲端資料保存

runner 每次都是新環境，不能依賴上次磁碟。cache 僅保存 `data/research-cache.db`，不保存 .env、Git 憑證或整個工作目錄。
key 包含 schema 版本、作業系統、config 雜湊、股票池識別及每次唯一的 run_id／attempt。
還原使用同設定及股票池的前綴，取得最近 cache；每次寫入新 key，避免不可變 cache 一直停留在第一天。
報表是 14 天 artifacts，SQLite 不寫回原始碼，也不作為報表 artifact。

cache 可被平台清理，且不是永久歷史備份。缺失時以截止日往前 90 天重建，通常涵蓋 3～4 個月份；
預設三檔約 9～12 個月份請求，遇暫時性錯誤依 config 重試與限流。小型股票池的歷史完整性仍需另外檢查。
中斷時 cache 可能來不及保存，下一次便有限重建；成功寫入的月份可在失敗 job 中盡量保存 cache。
GitHub cache 可能對儲存庫協作者及 PR 工作流程可讀，只放公開行情；不將憑證放入資料庫或輸出路徑。

### Actions 版本依據

已對照官方文件並將 Actions 固定為對應版本的 commit SHA：
[checkout v6](https://github.com/actions/checkout)、[setup-python v6](https://github.com/actions/setup-python)、
[cache v5](https://github.com/actions/cache)、[upload-artifact v7](https://github.com/actions/upload-artifact)。
Python 使用 3.12，依賴沿用 pyproject.toml 的 `matplotlib>=3.8,<4` 與 `setuptools>=64`，不新增應用套件。
安裝方式依 [GitHub Python CI 文件](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) 與
[Matplotlib 安裝文件](https://matplotlib.org/stable/install/index.html)；依賴採相容範圍，並非完全鎖定的環境。
排程限制見 [GitHub 事件文件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

### 本階段本機驗收（2026-10-01）

- 新研究流程 35 項、既有下載／重試 15 項、既有批次 resume 8 項相關測試通過。
- 建置 wheel 並安裝至 /tmp，使用專案 `.venv/bin/python` 從無 data/、reports/ 的乾淨目錄執行完整測試：287 項通過。此項使用本機虛擬環境的依賴；後續 GitHub 全新 runner 驗收另列於下方。
- 兩份 workflow 已通過 actionlint 1.7.12 靜態檢查（未使用 shellcheck）。
- 既有本機資料的 skip-download：9/29 全名單 1,054 檔，成功排名 1,026、排除 28，退出碼 2；JSON／CSV 均保存 1,026 筆。
- 三檔自動日期採 9/29，顯示距台北今日 10/1 為 2 天；三檔皆通過該日檢查，退出碼 0。
- 資料庫 SHA-256 驗證前後一致，實際報表只寫入新的 /tmp 目錄。未下載真實行情、未改動既有研究結果。
- 本機驗收後另完成下方 GitHub 手動驗收；定時觸發仍未啟用或驗證。

## GitHub 遠端驗收（2026-10-01）

研究程式版本：`a9db36767d50dfdef4de9c08786eefa0ecad1dc3`，推送至遠端 main，保留完整開發歷史。
兩次研究均手動指定 `stocks=2330 2317 2454`、`allow_partial=false`，as_of 留空自動選日。
沒有更新全上市名單或下載多年歷史，沒有啟用 `RESEARCH_SCHEDULE_ENABLED`。

| 執行 | 實際結果 |
|---|---|
| [手動 CI](https://github.com/kevinhuang-11/tw-stock-backtest/actions/runs/36880915103) | Ubuntu runner 安裝 Python 3.12 與專案依賴，從暫存目錄測試安裝後套件；287 項通過 |
| [第一次研究](https://github.com/kevinhuang-11/tw-stock-backtest/actions/runs/36880920030) | cache miss；有限初始化 2026-07-04～10-01，每檔請求 7、8、9、10 月，共 12 個月份請求 |
| [第二次研究](https://github.com/kevinhuang-11/tw-stock-backtest/actions/runs/36881332682) | 還原第一次 cache；每檔 previous_latest=2026-10-01，只重抓 10 月，共 3 個月份請求 |

兩次均以 2026-10-01 分析，距台北當日 0 天；三檔全部評估成功、排除 0，狀態 success／退出碼 0。
第一次每檔寫入 61 筆；第二次每檔 upsert 1 筆後，資料庫仍各有 61 筆。
`database-check.json` 兩次均記錄 `integrity_check=ok`、`duplicate_keys=0`，實際行情日期為 7/6～10/1。
此診斷只保存計數、日期與完整性檢查，不包含 SQLite 副本。

cache 日誌顯示第二次從以 `36880920030-1` 結尾的 key 還原，再保存以 `36881332682-1` 結尾的新 key。
驗收後共有兩份 cache，證明沒有覆寫不可變 key，也沒有每次重新建立初始化資料。

兩份 artifact 均已下載檢查：

- 各 8 個 JSON／CSV／TXT 檔案皆可讀；沒有資料庫、環境檔或私人筆記。
- 設定、股票池與 commit 符合實際執行，dirty=false，initial_days=90。
- JSON／CSV 都保存完整 3 檔排名，日期一致，excluded 為空。
- summary.report_complete=true，run 目錄沒有 .incomplete 後綴。
- 檢查輸出欄位及常見 Token／私鑰格式，未發現憑證；未匯出環境變數全集。

### 驗收界線

- 兩次真實研究均退出碼 0；此次沒有刻意製造真實 API 失敗來跑遠端 1／2 分支。
- 1／2 的研究結果由遠端 CI 的離線測試涵蓋，workflow 最後判定腳本另在本機以 0／2／1 執行，分別為成功／成功附 warning／失敗；非零狀態的完整雲端故障流程尚未實測。
- 寫入中斷的 .incomplete 行為由自動測試驗證，沒有故意中斷遠端 runner。
- 自動排程維持停用，沒有驗證定時觸發或長期每日穩定性。
- artifacts 保存 14 天、cache 可能被清理；本表為當次實際紀錄，不能替代永久資料備份。

## 本機網頁工作台與 Gmail

### 開啟與操作

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python -m tw_stock_backtest.cli.serve_workbench
```

瀏覽 `http://127.0.0.1:8765`。WSL 可從 Windows 瀏覽器開啟同一網址。
依序操作：

1. **既有報表**：讀取 `reports/` 的研究摘要與回測結果；未完成或無法解析的報表另外標示。
2. **新增研究**：輸入股票代號與分析日期。預設只用本機行情；明確勾選才連線更新，最多 10 檔、初始化 90 天。沿用原研究 CLI 的同日評分、增量、嚴格／部分成功規則。
3. **執行狀態**：重新整理查看 queued → running → success／partial／failed，以及日誌與摘要。
4. **排名比較**：選擇兩份完成的研究報表，查看新增、退出與名次變動；母體或設定不同會提示，不把相對名次變化解讀為策略改善。
5. **回測**：指定起迄日期及規則／多因子排名，沿用設定檔資金與費稅，結果包含策略、買進持有基準及圖表。使用自己已準備且獲准研究的期間。
6. **郵件預覽**：每個工作完成（包括失敗）後產生 `notification.eml`。點開可查看；這個網頁版本不寄出真實郵件。

可改變讀取與輸出位置：

```bash
.venv/bin/python -m tw_stock_backtest.cli.serve_workbench \
  --config config.toml --reports-dir reports \
  --state-dir /tmp/tw-stock-workbench --port 8765
```

`--reports-dir` 只供讀取；新工作存在 `--state-dir/<工作ID>/`，包含 `job.json`、`output.log`、`notification.eml` 與 `reports/`。
預設 state 在 `reports/workbench`，已被 Git 忽略。不修改既有報表，也不修改原 CLI 的輸出格式。

工作以一個背景 worker 依序執行，最多四個執行中／等待工作。研究／回測有 20 分鐘上限，繪圖另有 2 分鐘上限。
程式重啟時將殘留 queued／running 標為 interrupted，不自動重跑下載或寄信。
Ctrl+C 關閉會等待已排入的工作結束；強制終止後請檢查日誌與研究 `.incomplete`，必要時使用原 CLI 的 resume。
同一 state 目錄不可啟動兩個工作台；不同目錄與其他 CLI 不共用此鎖，請勿同時更新同一資料庫。

這是單人本機工具：綁定 loopback、關閉 debug、檢查 Host 與表單驗證碼。不要公開代理或部署。
[Flask 官方說明](https://flask.palletsprojects.com/en/stable/quickstart/)亦將內建伺服器定位為開發用途。
目前需手動重新整理，無多使用者帳號、排程、任務取消或自動重試。

### Gmail：先預覽，再自行手動寄送

不需要憑證即可預覽，預設使用 `preview@example.invalid` 作為收寄件人：

```bash
# 將路徑替換成工作台 job.json 或研究 summary.json；輸出檔須尚不存在
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary reports/workbench/工作ID/job.json \
  --dry-run --output /tmp/research-preview.eml
```

預覽使用與真實寄送相同的郵件建立函式。只有下列明確 `--send` 指令會連線 Gmail；工作台不讀取 Gmail 憑證、不寄信。

自行測試時：

1. 在自己的 Google 帳號啟用兩步驟驗證，再建立應用程式密碼。
   [Google 官方說明](https://support.google.com/accounts/answer/185833)列出可用條件；某些組織帳號或安全設定不支援。使用應用程式密碼，不是一般登入密碼。
2. 只在本機終端設定環境變數，不寫入 TOML、Git 或網頁表單。以下 Bash `read -s` 避免密碼出現在命令歷史／螢幕：

```bash
read -r -p '自己的 Gmail 地址: ' GMAIL_ADDRESS
export GMAIL_ADDRESS
read -r -s -p 'Gmail 應用程式密碼: ' GMAIL_APP_PASSWORD
export GMAIL_APP_PASSWORD
printf '\n'
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary reports/workbench/工作ID/job.json --send
unset GMAIL_ADDRESS GMAIL_APP_PASSWORD
```

使用 `smtp.gmail.com:465` 的 SSL 連線，寄件人與收件人固定相同。只支援個人 `@gmail.com` 地址，不使用 Gmail API 或 OAuth。
失敗回傳 1，只顯示錯誤類型；成功／預覽回傳 0。不自動重試寄送，因為連線中斷時郵件可能已被接受，重試可能重複寄信。
通知只挑選摘要欄位，不附完整資料庫、設定檔、日誌或環境變數。報表仍可能包含個人研究資訊，請自行保管。

### 本機驗證範圍

測試包含 Flask 表單與路徑限制、背景工作退出碼、重啟狀態、排名比較、離線研究／回測／圖表串接，以及模擬 SMTP 與 dry-run 不連線。
使用者已確認 Gmail 真實寄送與收件成功；後續內容變更僅以模擬與預覽驗證，不自動寄信。WSL Windows 瀏覽器連線仍需在自己的環境確認，不修改 GitHub 排程。

本階段於 2026-10-05 完成本機驗證：完整 **296 項測試通過**。使用既有三檔資料完成 9/24、9/29 排名比較與 9/1～9/29 回測，產生基準圖表及三份郵件預覽；本機 HTTP 回應 200，資料庫雜湊前後一致。
另確認指定 9/28 時會因最後行情為 9/24 而失敗，未偷偷改用其他日期。沒有下載行情、寄送郵件或執行遠端 workflow。

## 每日選股研究報告

### 網頁查看

重新啟動本機工作台後，在「既有報表」點選研究報表，即可查看：

- 分析日期、完整／部分結果、指定及實際評估範圍。
- 前 N 名候選股（預設 10，上限 50）、訊號收盤價與個股因子說明。
- 可比較的前期排名、新進／退出前 N、研究提示與排除原因。
- HTML／純文字郵件預覽、完整排名 CSV。完整排名表另提供股票代號搜尋及名次／代號／總分排序。

工作台以啟動時的 `--reports-dir` 與 `--state-dir` 搜尋歷史。網頁只預覽，不新增自動寄信功能。
舊工作已保存的 `notification.eml` 保留原內容；如需新版內容，使用報表明細的預覽連結或以下 CLI 產生新預覽。

### 使用你已完成的工作預覽

以下是目前存在的工作 ID。從專案根目錄執行，不需要 Gmail 憑證：

```bash
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary reports/workbench/4412a63a1df14eee855f2a58c21039be/job.json \
  --top 10 --history-dir reports \
  --dry-run --output /tmp/my-daily-research.eml
```

輸出必須尚不存在；再次產生請換一個檔名。產物為：

```text
/tmp/my-daily-research.eml
/tmp/my-daily-research.eml.preview/
    report.html             可用瀏覽器開啟的 HTML
    report.txt              純文字
    research-summary.json   共用摘要、數值、說明規則、前期來源與版本
    rankings.csv            原報表完整 CSV（來源缺少時不偽造附件）
    message.eml             HTML + 純文字替代內容 + CSV 附件
```

先寫入 `.preview.incomplete`，全部完成才改名；不覆寫原研究報表或舊預覽。
`--summary` 同時接受原研究 `summary.json`。若 `job.json` 沒有唯一完成的研究報表，維持執行狀態摘要，不猜測對應結果。
`--history-dir` 可重複指定；預設只搜尋目前目錄的 `reports/`，不掃描整個硬碟。

### 使用者明確手動寄送

依前節設定 Gmail 環境變數後，以下指令會**實際寄信**：

```bash
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary reports/workbench/4412a63a1df14eee855f2a58c21039be/job.json \
  --top 10 --history-dir reports --send
```

若希望寄送的內容與已看過的預覽完全一致，直接使用保存的衍生摘要：

```bash
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary /tmp/my-daily-research.eml.preview/research-summary.json --send
```

此模式保留預覽當時的日期、候選數與前期選取，不再重新搜尋歷史；要更新內容請從原研究報表重新 dry-run。
預覽與寄送共用同一 `build_message`；不加 localhost 連結。保持原本「一次明確呼叫寄一次、不自動重試」的行為，沒有新增寄送去重資料庫，重複手動 `--send` 仍會重複寄送。

### 說明依據與比較界線

- **不重新計算因子或排名**：讀取既有 `rankings.json` 的原始值、因子分數與總分；附件是原 `rankings.csv`。
- **加權貢獻**＝保存的因子分數 × 有效權重 ÷ 權重總和。零權重不列入主因；有效貢獻差距不超過 1 分時不誇大差異。
- **原始值**：動能是 N 期價格報酬；趨勢是短均線／長均線－1；波動是 N 個每日報酬的母體標準差。報告百分比僅為顯示單位，保存值不變。
- **集中門檻**：因子分數 ≥ 75 稱相對較前；低波動分數 ≤ 25 提醒相對起伏較大；至少兩檔候選股中，同一已知產業占比 ≥ 50% 才提示集中。未知產業單獨列出，不當成同一類。門檻集中於 `research_digest.POLICY`，不是新增選股因子。
- **前期選擇**：只選分析日期更早、已完成且成功／部分成功的報表。排名模式、因子期間與權重、指定股票池、實際排名母體都要相同；從相符者選最近分析日，同日多次以結束時間與 run_id 穩定選取。`top_n` 只是顯示限制，不影響完整排名比較。
- **不相符時**：標明設定／母體差異，不計算名次升降；前次或本次未排名的股票另列，不稱為新進前 N 或排名下跌。同日重跑不當成跨日變化。
- **資料缺漏**：新研究從當次使用的行情保存 `evaluations[].signal`（日期及收盤價）。舊報表缺少排名模式、收盤價、名稱或產業時顯示「未提供」，不查最新資料庫補值。僅指定代號的股票池通常沒有名稱／產業；使用名單檔的原報表才有該快照。
- **歷史行情**：標題前加註，顯示與預覽當日的日曆日差距；沒有交易日曆，不推定缺少幾個交易日。分數不是上漲機率，說明不提供目標價或交易建議。

### 本階段驗證（2026-10-05）

使用既有小型三檔與全名單報表（1,054 檔，成功 1,026、排除 28）產生 HTML、純文字與 CSV 附件預覽。指定歷史目錄沒有更早且可比較的結果，因此真實示例顯示「尚無可比較的前期結果」；名次升降、新進退出及母體差異以合成資料測試。
另以本機行情離線執行新三檔研究，確認訊號價保存；資料庫及來源報表雜湊不變。未下載行情、讀取 Gmail 憑證或寄送郵件。

測試涵蓋權重／負動能／同分／缺值、前期選擇、HTML 跳脫、CSV 位元組一致、舊報表、預覽中斷、Gmail 模擬，以及工作台端到端操作。完整測試 **307 項通過**。
本機 HTTP 已驗證報表、HTML／純文字預覽、CSV 與搜尋排序；目前沒有可用的瀏覽器自動化工具，未宣稱完成手機或 Gmail 各客戶端的實際版面驗證。

### 憑證與輸出邊界檢查（2026-10-05）

- Gmail 地址與應用程式密碼只由明確寄信函式讀取；網頁沒有憑證 API，背景研究只繼承列入白名單的執行環境變數。
- 工作台維持 loopback、Host 檢查與 POST 表單驗證碼。錯誤頁只顯示錯誤類型，不回傳原始例外內容。
- 舊報表、日誌與預覽輸出會遮蔽敏感欄位及常見秘密格式；不讀取真實環境憑證來比對。這不是能辨識任意無標籤秘密字串的保證，仍不可把憑證放進研究資料或設定。
- Actions artifacts 改為指定研究檔案清單；cache 僅為 `data/research-cache.db`。本機工作台與郵件預覽不在目前 workflow 的上傳清單內。
- 本機及已取得的 main 歷史未發現 `.env` 被追蹤或常見秘密格式；此結論不涵蓋已刪除的遠端物件、其他人的 clone 或任意無格式的密碼。

安全測試使用合成值，包含網頁／郵件遮蔽、子程序環境隔離、錯誤訊息及跨站請求限制；完整測試 310 項通過。本次沒有寄信、push、啟用排程或改寫歷史。

## 策略庫、技術指標與比較

### 發布與驗證界線

前階段工作台、研究摘要與安全修正已發布至 `main`：
`4caf55671dd851606c282a3eaecbb6afbcfa1b7e`。
[對應 GitHub CI](https://github.com/kevinhuang-11/tw-stock-backtest/actions/runs/37274585944) 在乾淨 runner 通過 310 項測試。
本節策略功能只完成本機實作，未 push、未啟用排程、未寄送真實郵件。

### 策略管理

策略使用 JSON schema 1，保存在 `strategies/`。只包含穩定 ID、名稱、說明、排名模式、指標期間、權重與條件。
股票池、日期、初始資金、配置、費稅與滑價仍在執行設定中，不接受寫入策略檔。

```bash
# 清單與完整內容／雜湊
.venv/bin/python -m tw_stock_backtest.cli.strategy_library list
.venv/bin/python -m tw_stock_backtest.cli.strategy_library show baseline

# 從目前設定建立；ID 需尚不存在
.venv/bin/python -m tw_stock_backtest.cli.strategy_library create my_strategy --name "我的示例"

# 複製，不改原策略
.venv/bin/python -m tw_stock_backtest.cli.strategy_library copy baseline my_copy --name "基準副本"

# 編輯 JSON 後驗證、原子儲存；更新現有 ID 必須明確 --replace
.venv/bin/python -m tw_stock_backtest.cli.strategy_library save --file strategies/my_copy.json --replace
```

也可啟動 `.venv/bin/python -m tw_stock_backtest.cli.serve_workbench`，開啟 `/strategies` 建立、查看、複製及修改。
期間與權重使用表單，AND 條件使用受限 JSON 清單編輯；沒有任意程式碼或巢狀運算樹。
工作台預設讀取 `strategies/`，可用 `--strategy-dir` 指定自己的策略庫；所有路徑由策略 ID 控制，不接受網頁傳入任意檔案位置。

### 選用策略

```bash
# 嚴格模式：條件未過不算錯誤，但指標資料不足會阻止排名
.venv/bin/python -m tw_stock_backtest.cli.run_research \
  --skip-download --as-of 2026-09-29 --stocks 2330 2317 2454 \
  --strategy strategies/rsi_range.json --output-dir reports/strategy-research

# 明確允許部分結果；保留每檔資料不足及條件結果
.venv/bin/python -m tw_stock_backtest.cli.run_research \
  --skip-download --as-of 2026-09-29 --stocks 2330 2317 2454 \
  --strategy strategies/rsi_range.json --allow-partial \
  --output-dir reports/strategy-research

.venv/bin/python -m tw_stock_backtest.cli.run_portfolio \
  --stocks 2330 2317 2454 --start 2026-09-01 --end 2026-09-29 \
  --strategy strategies/baseline.json --export --output-dir reports/strategy-backtest
```

優先順序為：原 config → 策略的選股欄位 → 明確 CLI `--top`。CLI 的股票、日期、資金等執行參數維持原本覆寫規則。
`run_portfolio --ranking` 若與策略模式衝突會拒絕，不偷偷改策略。未指定 `--strategy` 時保留原研究多因子／回測 rules 預設及運作方式。
`run_research` 與 `run_portfolio` 是新的策略選擇入口；原 `screen_factors` 等簡易 CLI 保持既有功能。

每次研究在 `summary.json`、回測在 `settings.json` 保存完整 `strategy_snapshot`（definition＋SHA-256）。
雜湊以穩定排序的 JSON 內容計算；名稱、期間、權重或條件修改都會改變版本識別，不只依靠名稱。
工作台在排入佇列時就另存策略副本，之後修改策略庫不影響已排入的工作。過去報表不回查目前策略檔。
舊版簡化報表匯出 API 若未提供完整設定，策略快照標為未提供，不補造。

### 指標公式與暖機

所有指標計算使用 Decimal；圖表才轉為 float。不承諾和所有看盤軟體的初值、歷史長度或柱狀值完全相同。

| 指標 | 定義與第一個有效值 |
|---|---|
| EMA(N) | 首 N 值的算術平均作種子；之後 `EMA + 2/(N+1) × (新值 − EMA)`。前 N−1 筆為 None |
| RSI(N) | Wilder 平滑：先以 N 次漲／跌幅平均初始化，後續 `(前平均×(N−1)+本次)/N`；`100×平均漲幅/(平均漲幅+平均跌幅)`。需要 N+1 個價格；全平盤 50、只漲 100、只跌 0 |
| MACD(F,S,M) | EMA(F)−EMA(S)，F<S；訊號線是前述 MACD 的 EMA(M)，以最初 M 個有效 MACD 平均初始化。MACD 需 S 筆，訊號線與柱狀值需 S+M−1 筆。柱狀＝MACD−訊號線，不乘 2 |
| 突破(N) | 當日收盤 **嚴格大於前 N 筆行情最高價的最大值**；需 N+1 筆，門檻排除當日最高價 |

EMA、RSI、MACD 的遞迴狀態從傳入歷史的第一筆開始，因此整個截至訊號日的輸入都要有效；不截取尾端任意重新初始化。
資料不足在指標序列中為 None，條件評估則標為 unavailable；缺價、非有限值或非法價格不補零、不刪列湊足期間。
突破與均線條件只驗證使用的窗口。所有條件先切到訊號日及以前，未來價格不影響結果。

**既有回測的已確認停牌處理仍保留。** 基準排名沿用原回測的行情處理；新 RSI／MACD 等條件另外讀取未刪列的原始歷史。
所以即使缺價是已確認停牌，遞迴指標仍可能無法計算；沒有新增補值或自動重置指標的規則。

### 條件與排名母體

有限的條件清單只採 AND；未列入表示不啟用：

```json
[
  {"type":"rsi","period":14,"minimum":"30","maximum":"70"},
  {"type":"macd","fast":12,"slow":26,"signal":9},
  {"type":"breakout","period":20},
  {"type":"trend","short_window":5,"long_window":20},
  {"type":"volume","period":20,"minimum":"1"}
]
```

RSI 使用含端點區間；MACD 嚴格大於訊號線；trend 為 Close>SMA(short)>SMA(long)；volume 為當日量／前 N 筆平均量嚴格大於 minimum。
`rules` 模式仍包含原有均線與量比規則，上列清單是額外條件；要自由選用單項趨勢／量比條件，可用 factors 模式。

多因子先對原本有效因子資料的股票池計分，再套用條件；條件不會重新正規化分數。
`ranking_population` 記錄評分母體、`scored_rankings` 保存篩選前分數、`rankings.json/CSV` 保存符合條件者，故候選名次可能跳號。
資料不足與 `filtered`（數值有效但未符合）分開記錄；每檔的 conditions 包含實際值、門檻、passed 與原因。

新策略流程中：資料完整但沒有候選者仍是成功（退出碼 0）；有資料錯誤的嚴格模式為 1，允許部分且尚有有效股票時為 2。
回測沿用原先對無法更新訊號持股的暫時保留規則；條件未通過則可按下一行情日調整持股。
研究報告與 Gmail 顯示策略名稱、schema／內容雜湊及候選觸發值；歷史排名比較也要求策略雜湊一致，避免不同條件混比。

### 同設定策略比較

```bash
.venv/bin/python -m tw_stock_backtest.cli.compare_strategies \
  --strategies strategies/baseline.json strategies/rsi_range.json strategies/macd_breakout.json \
  --stocks 2330 2317 2454 --start 2026-09-01 --end 2026-09-29 \
  --output-dir reports/comparisons
```

一次限 2～5 份不同 ID 的策略。網頁首頁也有同樣的比較表單，排入既有背景佇列。
同一次比較只載入一次行情，固定股票池、期間、現金、配置、費稅與滑價；共用一個買進持有基準。
不接受任意不同來源報表直接拼接。資產日期不一致時拒絕發布完成報表。

新目錄保存 `common.json`（共同設定、行情雜湊、程式版本）、`comparison.json/CSV`、`comparison.png`，以及每個策略原本完整的回測報表。
比較報表用相對路徑引用各策略報表；全部完成後才將 `.incomplete` 目錄改名。
無候選日與資料不足日可能重疊，二者不能直接相加當作交易日總數。

### 固定示例與本機驗證

三份示例只展示功能，沒有依回測結果調整：原多因子、RSI14 區間 30～70、MACD12/26/9 AND 前20筆突破。
2026-09-01～09-29 的既有三檔資料有 19 個共同行情日。本機比較結果：

| 策略 | 區間報酬 | 最大回撤 | 費稅 | 成交筆數 | 平均持股市值占比 | 無候選日 | 資料不足日 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 5.1022% | 3.9149% | 978 | 2 | 65.8770% | 0 | 0 |
| rsi_range | 10.6913% | 3.3785% | 6,087 | 5 | 50.2492% | 0 | 19 |
| macd_breakout | −2.5309% | 2.5309% | 5,809 | 4 | 8.0432% | 16 | 19 |

共用買進持有基準報酬為 5.21505%。RSI／MACD 的歷史輸入中，2317 有缺價，不能刪除該列，因此兩份策略在各訊號日都有該股票的資料不足紀錄；不是三檔皆可正常使用新指標。
相同資料下，9/29 RSI 嚴格研究會失敗；明確 `--allow-partial` 才產生其餘兩檔候選。這是資料處理驗證，不是選出最佳策略，也不是樣本外有效性驗證。

本機測試涵蓋 EMA／MACD 手算值、Wilder RSI、平盤與單向行情、缺值／暖機、突破排除當日、未來資料不影響結果、策略驗證與快照、原因子／rules 基準相容、CLI 覆寫、網頁策略操作及比較費稅一致性。
HTTP 已驗證策略表單、複製、背景比較、比較頁、PNG 及含策略條件的郵件預覽。資料庫雜湊未變；沒有可用瀏覽器自動化工具，未宣稱已驗證實際瀏覽器排版。

策略與跨期間階段本機完整 **326 項測試通過**；安裝後完整操作與缺值影響見下節。遠端驗證以對應提交的 Actions 結果為準。

## 跨期間策略驗收與評估

### 操作

沿用同一比較 CLI；`--periods` 與 `--start/--end` 互斥。

```bash
# 已看過的三個探索期間，不讀取 2025 年保留期間績效
.venv/bin/python -m tw_stock_backtest.cli.compare_strategies \
  --strategies strategies/baseline.json strategies/rsi_range.json strategies/macd_breakout.json \
  --stocks 2330 2317 2454 \
  --periods experiments/periods_demo.json \
  --output-dir /tmp/tw-period-comparison

# 工作台首頁的「跨期間策略比較」使用相同核心
.venv/bin/python -m tw_stock_backtest.cli.serve_workbench

# 研究與郵件預覽；REPORT_DIR 換成指令印出的這次報表目錄
.venv/bin/python -m tw_stock_backtest.cli.run_research \
  --strategy strategies/rsi_range.json --skip-download \
  --as-of 2026-09-29 --allow-partial --output-dir /tmp/tw-strategy-research
.venv/bin/python -m tw_stock_backtest.cli.notify_report \
  --summary REPORT_DIR/summary.json --dry-run --output /tmp/tw-strategy-preview.eml
```

期間 JSON 每筆包含 `id`、`start`、`end`、`purpose`、`previously_seen`。
`purpose` 可為 `development`（開發／探索）或 `validation`（驗證）；
`previously_seen` 記錄是否已看過结果。標為驗證不代表真正樣本外；看過或用來調參後不能稱為未見的最終測試集。
最多 12 個期間、2～5 份策略，不自動搜尋參數或挑選勝者。

每段獨立重置初始現金與持股，共用股票池、配置、費稅與滑價。
行情先固定為一次輸入快照，各期間只傳入截至該段結束日的資料，開始日前行情僅供暖機。
沒有交易日曆時採保守邊界規則：**每檔在指定起、迄日都必須有行情列**；否則記錄失敗，不改成附近日期。
這也會拒絕週末作為邊界；請自行明確選擇已保存的行情日。資料最早／最晚日期不代表中間歷史完整。

### 報表與退出碼

每次產生獨立目錄，包含：

- `common.json`：股票池、成本配置、資料來源、覆蓋範圍、輸入雜湊、策略快照與程式版本。
- `progress.json`：各期間目前執行狀態；工作台重新整理可查看 `.incomplete` 的進度。
- `comparison.json`／`comparison.csv`：各策略各期間報酬、基準報酬、雙方最大回撤、報酬差（百分點）、費稅、成交數、平均持股市值占比、無候選及不足日數。
- 各期間目錄：原有策略比較圖、共同基準與各策略完整回測報表；彙總的 `report` 為相對路徑，可追查原始資產曲線與交易。

退出碼：`0` 全部完成且沒有無法評估日，`2` 有完成結果但部分期間／策略失敗或資料不足，`1` 沒有可保存的成功子回測或流程失敗。
單一子策略失敗仍保留其他策略；失敗原因與類型在 JSON 中，未產生報酬的項目不放入績效 CSV。
中斷留在 `.incomplete`；最後完成標記及目錄改名後才視為正式結果。正式結果仍可能是部分成功或失敗，需讀取 `status`。

「有效期間」只計入該策略沒有無法評估日且成功完成的期間；其他完成結果照樣列出，但不加入跑贏基準的分母與指標範圍。
報酬 `0.05` 表示 5%；策略 5% 與基準 3% 的差是 **2 個百分點**。
不加總獨立期間報酬。重疊期間會列入 `overlapping_periods`，不得視為獨立樣本；跑贏期間比例也不是未來獲利機率。

### 遞迴缺值規則與實際影響

RSI／MACD 檢查該股票已載入的**第一筆行情至訊號日**的全部收盤价，種子與遞迴沿用原公式。
任意早期缺價都會持續阻擋後續計算，沒有自動重置或有限暖機裁切；排名與回測條件使用同一個原始行情前綴。
一般因子與均線等局部窗口仍依既有規則計算。已知停牌的缺價也不會從遞迴輸入中刪除。
診斷保存 `input_range`（起訖及筆數）、`invalid_inputs`（日期、從 1 起算的位置、欄位）與原因。

2026-10-06 唯讀盤點，截至 9/29 的已存資料有 **33 檔、95 筆收盤缺值**。
這是遞迴輸入的缺值盤點，不等同 33 檔是當日唯一無法評估股票；也未將未知缺漏判定為停牌。
三檔示例各 547 筆，資料起點 2024-01-02、截止 2026-09-29。
2317 的第 380 筆（2025-07-30）收盤缺失，造成以下 40 個示例訊號日的 RSI／MACD 條件無法評估；2330、2454 沒有收盤缺值。

後續若要允許缺價後重新累積暖機，應建立**明確的新策略版本**，先定義連續有效資料段與重置種子，再與目前公式做敏感度比較。
本次不更改預設，不刪掉缺價列拼接日期，也不以提高評估成功數為目標。

### 本機實際結果（已看過的探索資料）

三檔股票、三份固定示例策略；每段重置 100 萬元，其他設定沿用 `config.toml`，不調參。

| 期間 | baseline | rsi_range | macd_breakout | 買進持有基準 |
|---|---:|---:|---:|---:|
| 2026-08-03～08-31 | -2.1540% | -1.0915% | 0.0000% | 0.94255% |
| 2026-09-01～09-15 | -0.5978% | 1.5249% | -0.4799% | -0.24895% |
| 2026-09-16～09-29 | 4.0508% | 7.5172% | -2.0510% | 5.2237% |

baseline 跑贏基準 0／3 個有效期間。RSI 與 MACD 策略分別有 21、11、8 個無法評估日，因此有效期間分母均為 0；不得據上述報酬宣稱較佳。
三段互不重疊，但相鄰市場環境仍可能相關。原始價未調整公司行動、股票池事後指定，結果僅展示可追查流程。

### 安裝與介面驗收範圍

已用 wheel 安裝至獨立暫存目錄，離開原始碼目錄執行：建立策略 → 複製修改 → 排名 → 回測 → 比較 → 郵件 dry-run。
研究與回測有效策略雜湊相符，原 SQLite 雜湊不變，未呼叫 SMTP。
策略 JSON 與 TOML 是使用者輸入，**不隨 wheel 內建**；安裝後請明確提供 `--config`、`--strategy`／`--strategies`，工作台提供 `--strategy-dir`。
Jinja HTML 模板隨套件安裝；此工作台沒有另行編譯的 JS 或 CSS 產物。

HTTP／整合測試涵蓋策略表單、背景比較、CSV、郵件預覽、Host 與 CSRF 防護。
**尚未使用真實瀏覽器驗證桌面或窄螢幕版面。** 人工點選清單：

1. 開啟首頁與「管理策略庫」，建立、複製、修改條件，確認列表與雜湊更新。
2. 在寬桌面與約 390px 視窗下填入跨期間表單；確認文字可讀、比較表可水平捲動。
3. 送出工作、重新整理狀態，打開正式報表及子回測、比較圖與 CSV。
4. 查看部分成功原因與郵件預覽；確認操作沒有自動寄信。

2026-10-06 遠端驗收：提交 `2f2290b` 的 [策略與跨期間 GitHub CI](https://github.com/kevinhuang-11/tw-stock-backtest/actions/runs/37401510494) 在 Python 3.12 乾淨 runner 安裝套件後，完整 **326 項測試通過**。本機工作分支為 `refactor/package-layout`，正常推送至既有 `main`；未啟用排程或寄信。
