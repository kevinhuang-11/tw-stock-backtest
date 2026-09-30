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
若使用已有全名單，可改跑 README 的 `--universe ... --allow-partial` 命令，說明成功與失敗數量。
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
