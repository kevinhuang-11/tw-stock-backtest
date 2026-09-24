## 開發環境安裝

需要 Python 3.12 或以上版本。

在專案根目錄執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

使用 editable 模式安裝後，修改 src 中的 Python 程式碼通常不需要重新安裝。

## 設定

執行設定集中於根目錄的 config.toml，包括：

- 股票池與選股條件
- 策略均線期間
- 初始資金與每次買進股數
- 手續費、交易稅與取整方式
- 下載逾時與請求間隔
- 資料庫位置

支援的命令列參數優先於設定檔，且不會修改設定檔。

各入口皆支援 --config 指定其他設定檔。
資料庫相對路徑以設定檔所在資料夾為基準。

## 執行方式

以下指令在專案根目錄、啟用虛擬環境後執行。

下載行情：

```bash
python -m tw_stock_backtest.cli.fetch_stock --stock 2330 --start 2026-07-01 --end 2026-09-22
```

查看資料庫：

```bash
python -m tw_stock_backtest.cli.inspect_db --stock 2330 --limit 5
```

分析均線與交易訊號：

```bash
python -m tw_stock_backtest.cli.analyze_stock --stock 2330 --start 2026-07-01 --end 2026-09-22
```

使用設定檔股票池進行篩選：

```bash
python -m tw_stock_backtest.cli.screen_stocks --as-of 2026-09-22
```

執行單一股票回測：

```bash
python -m tw_stock_backtest.cli.run_backtest --stock 2330 --start 2026-07-01 --end 2026-09-22
```

分析、篩選與回測讀取本機資料庫，請先下載所需股票與日期區間的行情。

## 測試

```bash
python -m unittest discover -s tests -v
```

## 程式結構

- src/tw_stock_backtest/data/：行情轉換與資料庫操作
- src/tw_stock_backtest/analysis/：指標、交易訊號與選股規則
- src/tw_stock_backtest/backtesting/：回測引擎與費稅計算
- src/tw_stock_backtest/cli/：命令列入口，目前也包含下載函式
- tests/：自動化測試
- data/：本機資料庫，不納入 Git
- reports/：預留報表輸出目錄

## 目前限制

- 回測以訊號後下一筆行情的開盤價模擬成交。
- 費稅依設定計算，尚未模擬滑價、股利、拆股與精確零股成交。
- 多股票篩選已有候選股排名，尚未完成共用資金的多股票組合回測。
- 選股排名是規則計算結果，不代表未來獲利機率。

## 選股與回測模式

### 規則版

收盤價高於短期均線，短期均線高於長期均線，
且量比高於設定門檻，再依價格動能排名。

```bash
python -m tw_stock_backtest.cli.run_portfolio --start 2026-08-01 --end 2026-09-22 --ranking rules --export
```

### 因子版

將動能、均線趨勢與低波動轉成股票池內的相對排名分數，
依設定權重加權，選取排名前幾檔。

目前不設最低分數門檻。

```bash
python -m tw_stock_backtest.cli.screen_factors --as-of 2026-09-22
```

```bash
python -m tw_stock_backtest.cli.run_portfolio --start 2026-08-01 --end 2026-09-22 --ranking factors --export
```

兩種策略共用交易引擎：當日收盤產生名單，
下一行情日開盤先賣後買，每次買進固定股數，
使用共同資金與設定的費稅。

## 範例結果

股票池：2330、2317、2454。
指定期間：2026-08-01～2026-09-22。
實際行情期間：2026-08-03～2026-09-22。
初始資金：1,000,000 元。

| 指標 | 規則版 | 因子版 | 買進持有 |
|---|---:|---:|---:|
| 報酬率 | 0.9835% | 10.9460% | 12.7828% |
| 最大回撤 | 6.5934% | 5.7802% | 7.0171% |
| 累計費稅（元） | 11,315 | 4,790 | 1,420 |
| 成交筆數 | 15 | 10 | 3 |

這是本次資料與設定的示範結果，不代表其他期間的表現。
兩種策略的持股時間不同；買進持有基準則採等預算配置，
與策略固定股數的配置方式不同。

## 報表與繪圖

使用 --export 會在 reports 下建立獨立資料夾，包含：

- settings.json：實際設定與指定日期。
- results.json：策略模式、完整結果與績效。
- input_data.json：本次使用的行情快照。
- trades.csv：策略與基準成交紀錄。
- equity.csv：每日資產比較。

將下列路徑替換成實際匯出資料夾：

```bash
python -m tw_stock_backtest.cli.plot_report --report-dir reports/你的報表資料夾
```

圖表輸出為該資料夾中的 performance.png。

## 驗證與限制

- 使用 unittest 驗證計算、資料庫操作、下載失敗與重試。
- 端到端測試涵蓋規則模式的回測、匯出與繪圖。
- 因子模式另有歷史排名及回測引擎串接測試。
- 模擬未包含滑價、股利、拆股及精確零股成交。
- 期末持股按收盤價估值，不強制賣出。
- 回測期間若股票池內個別股票缺行情，程式會拒絕回測。
- 尚未獨立核對交易日曆，全部股票共同缺少的日期可能無法發現。
- 目前示範股票池小、期間短，尚未完成長期間及樣本外驗證。