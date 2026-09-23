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