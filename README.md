# 台股研究與回測工作台

以官方公開資料做台股選股排名、歷史回測與自選股追蹤的本機工具。Python 抓取證交所行情與營收法人資料存進 SQLite，依策略產生候選排名、模擬多股票回測並與買進持有比較，所有結果都保存成不可覆寫的報表。重點是**不偷看未來資料、計入台股費稅、資料不足時直接說明**。

v1 版本供單人在本機使用；測試驗證的是軟體行為，不代表任何策略已證明能獲利。

**Python 3.12 · Flask · SQLite · Decimal · Matplotlib · 原生 JavaScript · unittest · GitHub Actions**

[操作導覽](docs/DEMO.md) · [操作與設計細節](docs/OPERATIONS.md)

## 一行試用（離線示範）

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e . && .venv/bin/python examples/prepare_demo.py --serve
```

開啟 **http://127.0.0.1:9000**。示範使用三檔合成股票，不需要網路、不讀取任何個人資料；示範環境中連網更新的按鈕會停用並說明原因。

![總覽：資料狀態、最近的工作與報表](docs/images/overview.png)

## 技術亮點

- **不使用未來資料**：訊號以當日收盤計算、下一個行情日開盤成交；指標只看分析日以前的資料，有專門測試確認加入未來行情不會改變過去結果（[portfolio.py](src/tw_stock_backtest/backtesting/portfolio.py)、[test_factors.py](tests/test_factors.py)）。
- **計入台股成本**：手續費 0.1425%（最低 20 元、可設折扣）、賣出證交稅 0.3%、單邊滑價，全程以 `Decimal` 計算並依規則捨去（[costs.py](src/tw_stock_backtest/backtesting/costs.py)）。
- **資料品質優先於結果好看**：缺價不補零、不刪列湊窗口，未確認的缺漏不當成停牌；介面標示每檔資料截至哪天、落後幾個行情日（[data_status.py](src/tw_stock_backtest/data_status.py)）。
- **可追查的報表**：每份研究與回測都固定保存當時的設定、股票池、輸入資料與策略 SHA-256，之後修改策略或更新資料都不會改變舊報表。
- **離線示範與連網防護**：合成示範名單會讓所有下載入口在發出請求前拒絕執行，測試以攔截網路函式確認（[test_offline_demo.py](tests/test_offline_demo.py)）。
- **不依賴前端框架與 CDN**：Flask＋Jinja＋原生 JS，自繪 SVG 圖示、系統字型；可鍵盤操作的股票選擇器；台股慣例紅漲綠跌，狀態標籤刻意不用綠色以免與下跌混淆。
- **工程流程**：離線單元與整合測試、CI 以安裝後的套件從乾淨目錄執行測試；Playwright 只在開發依賴，用來截圖與檢查瀏覽器互動（[tools/](tools/)）。
- **AI 協作的約束**：開發時與 AI 工具共用 [AGENTS.md](AGENTS.md) 與 [CLAUDE.md](CLAUDE.md) 的規則，例如不得使用分析日之後的資料、未經同意不檢視保留期間的績效、不自動下載全市場資料。

## 畫面

以下截圖使用本機真實行情（資料截至 2026-10-14，股票池為 2330、2317、2454），只展示功能，**不是投資建議或策略績效證明**。

| 自選股 | 個股 |
|---|---|
| ![自選股：條件狀態、研究名次、營收與法人](docs/images/watchlist.png) | ![個股頁：資料截至日期、重點數字、K 線與指標](docs/images/stock.png) |

| 研究排名 | 回測報表 |
|---|---|
| ![研究排名：候選股、總分與名次變化](docs/images/research-report.png) | ![回測報表：與買進持有並列的重點數字、資產與回撤](docs/images/backtest-report.png) |

<details>
<summary>手機寬度：建立研究</summary>

<img src="docs/images/mobile-new-research.png" alt="手機寬度的建立研究頁：股票以可移除的標籤呈現" width="320">
</details>

## 功能一覽

- **官方資料整合**：TWSE 行情、上市普通股名單、月營收與法人買賣超；來源分開保存，支援修訂追查。
- **策略篩選與排名**：策略庫、RSI／MACD／突破等有限條件、多因子相對排名，保存有效設定與雜湊。
- **回測與比較**：多股票、費稅與滑價、買進持有基準；多策略、多期間採相同設定獨立模擬。
- **個股與自選研究**：價格／指標圖、策略條件、營收與法人覆蓋；私人筆記與研究產物分離。
- **增量與背景更新**：月份規劃、有限重試、接續進度、背景佇列、取消與失敗診斷。
- **不可覆寫的研究快照**：保存所用數值與來源，區分條件改變、資料更新及同期間修訂。
- **共用報告內容**：HTML、純文字與郵件預覽共用摘要，CSV 不包含私人筆記。
- **工程驗證**：離線單元／整合測試、安裝套件測試與 GitHub CI；不需真實 API 或寄信憑證。

## Architecture

```text
TWSE prices                    TWSE / IIH supplemental sources
     |                                      |
     v                                      v
cli/ + data/                 cli/update_supplemental + data/supplemental
     |                                      |
     v                                      v
SQLite: stock_prices           SQLite: source-separated supplemental
     |                                      |
     v                                      |
strategies + analysis                       |
     |                                      |
     +--> backtesting --> reporting         |
     |                       |              |
     +--> saved rankings ----+--------------+
                             |
                             v
                    watchlist_research
                    values + snapshots
                             |
                             v
                 research_digest / notifications
                    HTML / text / CSV / preview

Workbench (Flask) / CLI: controlled jobs, queries and report access
Private notes SQLite: workbench only; excluded from report exports
```

這張圖要表達的是資料的流向：來源 → 分開儲存 → 共用的分析與研究核心 → 固定的報告，而不是資料夾清單。
圖中有兩條線刻意不存在：

- 營收與法人目前只供研究展示，**不送入排名或回測引擎**。
- 私人筆記只留在工作台，不會進入任何匯出的報告。

工作台與 CLI 呼叫共用核心，沒有另外實作一套交易模擬。

## Quick Start

WSL2 Ubuntu 24.04／Python 3.12。在專案根目錄執行；已有 `.venv` 不需重建：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m tw_stock_backtest.cli.serve_workbench --port 9000
```

開啟 **http://127.0.0.1:9000**。只有本機 loopback 監聽；無資料時首頁會說明從哪裡開始，不自動下載。上方導覽列可前往總覽、研究、回測、自選、個股、策略與報表。

新環境可依 [DEMO](docs/DEMO.md) 建立**無網路、無私人資料的合成範例**；使用真實資料的準備方式見 [操作指南](docs/OPERATIONS.md#真實資料準備)。資料庫相對路徑以設定檔所在目錄為基準。

## Demo

完整的離線示範步驟見 [操作導覽](docs/DEMO.md)：啟動 → 自選總覽 → 個股頁 → 保存與比較快照 → HTML 與郵件預覽。

下面是先前以真實歷史行情匯出的回測圖表，**不是合成範例的績效**；假設見圖注，只用來展示報表能力。

![既有回測報表：策略與買進持有基準的資產及回撤](docs/demo-assets/performance.png)

## Data Sources

| 來源 | 用途與界線 |
|---|---|
| [TWSE 月行情](https://www.twse.com.tw/zh/trading/historical/stock-day.html) | OHLC、量與成交資訊；用於技術研究及回測，原始價格未調整除權息／拆股 |
| [TWSE OpenAPI](https://openapi.twse.com.tw/) | 上市公司最新月營收快照；無任意歷史年月參數 |
| [TWSE IIH](https://wwwc.twse.com.tw/IIH2/zh/company/financial.html?code=2330) | 滾動歷史月營收；原單位元，精確換算千元，不與其他來源拼接分母 |
| [TWSE T86](https://www.twse.com.tw/zh/trading/foreign/t86.html) | 法人買進／賣出／淨額，單位股；依本機行情日期檢查累計覆蓋 |

## Design Decisions

1. **來源與修訂分開追查**：不同營收來源獨立保存；同一資料鍵的來源／單位衝突拒絕覆蓋，修訂保存舊值。
2. **保存快照而非回查最新設定**：有效策略與數值隨報表保存；完成前使用 `.incomplete` 目錄，過去快照不因修改策略而改變。
3. **隔離補充資料失敗**：營收或法人失敗仍可查看行情與策略；缺值不補零，更新結果與可用數值分開呈現。
4. **共用摘要呈現**：網頁與郵件預覽使用同一份結構化摘要與呈現函式，避免兩套文字規則不一致。

## Testing

```bash
# 完整測試；結尾的 "Ran N tests" 會顯示實際執行的項數
.venv/bin/python -m unittest discover -s tests -v

# 只跑一個測試檔
.venv/bin/python -m unittest discover -s tests -p 'test_portfolio_backtest.py' -v

# 只跑名稱符合的測試
.venv/bin/python -m unittest discover -s tests -k future -v
```

測試全部離線執行，不需要網路或任何憑證。涵蓋範圍包含未來資料隔離、策略與交易成本、資料修訂、快照比較與安全邊界。

CI 會在乾淨的 runner 上安裝套件，再執行同一組測試。另有一個研究 workflow 會取得行情並產出排名，只能在 GitHub Actions 頁面手動觸發，沒有排程。

## Project Structure

```text
src/tw_stock_backtest/
  cli/                 執行入口與更新流程
  data/                官方資料解析與持久化
  analysis/            指標、篩選與評分
  backtesting/         成交、資金、成本及基準
  templates/ static/   本機工作台介面
  *research*.py        研究流程、摘要與快照
  reporting.py         報表輸出
strategies/            少量示例策略
examples/              隔離的離線展示準備
tools/                 開發用：截圖與瀏覽器互動檢查（需 dev 依賴）
tests/                單元、整合與安全測試
docs/                 展示、操作與作品說明
```

根目錄 `data/`、`reports/` 與私人 state 屬本機產物，不納入 Git；`src/.../data/` 是必要原始碼，會公開。

## Limitations

- 三檔 IIH 實測歷史為 **2025-09～2026-09，共 13 個月**；可展示最近 12 月，但不是任意年份歷史，年增率仍可能缺少精確對應期。
- 缺少明確公告時間及完整口徑資訊；現在取得的修訂值不是完整的歷史當時可知資料（point-in-time）。
- 法人 20 日示例只有 17 筆時如實顯示不足；沒有獨立交易日曆，不把日曆日落差當成缺漏交易日。
- 目前上市股票池存在倖存者偏差；價格未調整公司行動，成交為簡化模擬。
- 已做 HTTP／整合驗證，**尚未完成瀏覽器桌面及窄螢幕 E2E**。工作台未設計為公開或多人服務。

## Future Work

- 補足可追查公告時間與修訂版本的歷史資料。
- 加入交易日曆及公司行動資料，改善研究期間的完整性判定。
- 建立瀏覽器操作與視覺回歸測試。
- 視使用需求提供容器化的本機安裝方式。
