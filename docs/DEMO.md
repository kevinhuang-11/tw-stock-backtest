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
