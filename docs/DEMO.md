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
本次只建立本機檔案，沒有 push、觸發 Actions 或啟用定時變數。

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
- 建置 wheel 並安裝至 /tmp，使用專案 `.venv/bin/python` 從無 data/、reports/ 的乾淨目錄執行完整測試：287 項通過。此驗證仍使用本機虛擬環境的依賴；GitHub 全新 runner 安裝須 push 後再驗證。
- 兩份 workflow 已通過 actionlint 1.7.12 靜態檢查（未使用 shellcheck）。
- 既有本機資料的 skip-download：9/29 全名單 1,054 檔，成功排名 1,026、排除 28，退出碼 2；JSON／CSV 均保存 1,026 筆。
- 三檔自動日期採 9/29，顯示距台北今日 10/1 為 2 天；三檔皆通過該日檢查，退出碼 0。
- 資料庫 SHA-256 驗證前後一致，實際報表只寫入新的 /tmp 目錄。未下載真實行情、未改動既有研究結果。
- 尚未驗證 GitHub runner、遠端 cache 保存／還原、artifact 下載與排程觸發；未 commit、push 或啟用任何 repository variable。
