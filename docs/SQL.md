# 資料庫與 SQL

[README](../README.md) · [操作導覽](DEMO.md) · [操作與設計細節](OPERATIONS.md)

這份文件說明行情庫的 schema、索引、版本遷移，以及在資料庫端完成的資料品質查詢。
所有查詢都使用參數化（`?` 或 `:name`），讀取一律以 `mode=ro` 開啟，不會因為路徑錯誤而建立空檔案。

## 三個資料庫

| 資料庫 | 程式 | 內容 |
|---|---|---|
| 行情庫 `stock_prices`（預設 `data/stocks.db`） | [data/database.py](../src/tw_stock_backtest/data/database.py) | 每檔每日的開高低收、量、值、筆數 |
| `supplemental.sqlite3`（行情庫旁） | [data/supplemental.py](../src/tw_stock_backtest/data/supplemental.py) | 月營收與法人，依來源分開，修訂時保留舊值 |
| `watchlist.sqlite3`（工作台 state） | [stock_research.py](../src/tw_stock_backtest/stock_research.py) | 自選清單與私人筆記，不進任何匯出 |

本文以行情庫為主；版本遷移目前只套用在行情庫（見最後的「限制」）。

## 行情表

```sql
CREATE TABLE IF NOT EXISTS stock_prices (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,                 -- ISO 8601，字串比較即日期比較
    open TEXT,                          -- Decimal 的字串；缺價為 NULL
    high TEXT,
    low TEXT,
    close TEXT,
    volume INTEGER NOT NULL CHECK (volume >= 0),
    turnover INTEGER NOT NULL CHECK (turnover >= 0),
    trade_count INTEGER NOT NULL CHECK (trade_count >= 0),
    PRIMARY KEY (stock_id, date)
)
```

- **價格存成字串**：SQLite 沒有十進位型別，`REAL` 會把 `10.15` 存成近似值。字串保存後在 Python 轉回 `Decimal`，費稅與資金計算全程不經過浮點數。代價是 SQL 不能對價格做數值運算，所以本專案的 SQL **只處理日期、筆數與 NULL**，價格計算一律在 Python。
- **缺價保留為 NULL**：來源當天沒有開高低收時（停牌或只有零星零股成交），列照樣寫入、價格為 NULL，不補值也不刪列。
- **寫入用 UPSERT**：`INSERT … ON CONFLICT(stock_id, date) DO UPDATE`，重抓同一個月不會重複。

## 索引與理由

主鍵 `(stock_id, date)` 的自動索引已涵蓋「單一股票＋日期範圍」的查詢（讀行情、找下一個行情日、自選頁的筆數），這類查詢不需要新索引。
缺的是**以日期為主**的查詢，以及**找缺價列**：

```sql
-- 行情日清單、某日之後還有幾個行情日、某一天的所有股票
CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON stock_prices(date);

-- 部分索引：只收錄收盤價為 NULL 的列（全庫 54,410 筆中的 111 筆）
CREATE INDEX IF NOT EXISTS idx_stock_prices_missing_close
    ON stock_prices(stock_id, date) WHERE close IS NULL;
```

### 查詢計畫與時間

量測環境：真實行情庫的**複本**（1,053 檔、673 個行情日、54,410 筆），SQLite 3.45.1，WSL2。
「前」是現有使用者的資料庫（schema 版本 0），「後」是對同一份複本執行遷移後的結果；未執行 `ANALYZE`。每項重複 200 次取平均。

| 查詢 | 加索引前 | 加索引後 |
|---|---|---|
| 行情日清單<br>`SELECT DISTINCT date … ORDER BY date` | `SCAN … USING COVERING INDEX sqlite_autoindex_stock_prices_1`<br>`USE TEMP B-TREE FOR DISTINCT`<br>**7.49 ms** | `SCAN … USING COVERING INDEX idx_stock_prices_date`<br>（不需臨時 B-tree）<br>**1.65 ms** |
| 某日之後的行情日數<br>`COUNT(DISTINCT date) … WHERE date > ?` | `USE TEMP B-TREE FOR count(DISTINCT)`<br>`SCAN … sqlite_autoindex_stock_prices_1`<br>**1.60 ms** | `SEARCH … USING COVERING INDEX idx_stock_prices_date (date>?)`<br>**0.20 ms** |
| 某一天的所有股票<br>`… WHERE date = ?` | `SCAN stock_prices`<br>**2.11 ms** | `SEARCH … USING INDEX idx_stock_prices_date (date=?)`<br>**0.47 ms** |
| 缺價列<br>`… WHERE close IS NULL` | `SCAN … USING INDEX sqlite_autoindex_stock_prices_1`<br>**3.65 ms** | `SCAN … USING INDEX idx_stock_prices_missing_close`（只有 111 項）<br>**0.034 ms** |
| 單檔區間（`load_records`） | `SEARCH … sqlite_autoindex_stock_prices_1`，0.17 ms | 不變，0.19 ms |
| 各股最新日（`GROUP BY stock_id`） | `SCAN … COVERING INDEX sqlite_autoindex_stock_prices_1`，4.0 ms | 不變，4.2 ms |

最後兩列是對照：它們原本就走主鍵，加索引不會也不應該改變。

**代價**：檔案由 4.69 MB 增為 5.71 MB（+21.6%）。把整份 54,410 筆寫進新資料庫由 93 ms 增為 125 ms；重寫其中 5,265 筆（UPSERT）由 7.0 ms 增為 16.0 ms。以每天增量更新一千多檔的規模，這個成本可以忽略。

測試 [test_database_sql.py](../tests/test_database_sql.py) 以臨時資料庫檢查同樣的計畫變化（SCAN／臨時 B-tree → 使用索引），並確認有無索引時查詢結果相同。

## Schema 版本與遷移

版本記在 SQLite 檔頭的 `PRAGMA user_version`。程式內有一份依序編號的遷移清單：

```python
MIGRATIONS = (
    (1, (TABLE,)),      # 行情表；舊版程式建立的資料庫已有此表，這一步不會改動它
    (2, INDEXES),       # 日期索引與缺價部分索引
    (3, CORPORATE_ACTIONS),  # 公司行動表（見下一節）
    (4, CORPORATE_ACTION_COVERAGE),  # 公司行動已下載的期間
    (5, TRADING_RESTRICTIONS),  # 處置與全額交割的期間與已下載範圍（見 HALT-POLICY.md 3.17、3.18）
)
```

規則：

- **只新增、不改寫**：每一步都是 `CREATE … IF NOT EXISTS`，不修改既有列，重複執行沒有副作用。
- **一個版本一個交易**：以 `BEGIN IMMEDIATE` 取得寫入鎖，執行該版本的敘述並設定 `user_version`，一起 `COMMIT`；任何一句失敗就整個版本 `ROLLBACK`，版本號維持不變。
- **取得鎖之後才讀版本**：兩個程序同時升級時，後到的會看到已完成的版本而略過。
- **拒絕較新的資料庫**：檔案版本高於程式支援的版本時，寫入會失敗並提示更新程式，不會降級。
- **何時升級**：舊版程式建立的資料庫 `user_version` 為 0。下次寫入行情（`save_records`）時自動升級；也可以不更新資料、直接執行：

```bash
# 只看版本，不修改；需要升級時退出碼為 2
.venv/bin/python -m tw_stock_backtest.cli.migrate_db --config config.toml --check
# 升級
.venv/bin/python -m tw_stock_backtest.cli.migrate_db --config config.toml
```

`PRAGMA user_version = N` 不支援參數綁定，這是程式中唯一用字串組出的 SQL；`N` 來自上面清單中的整數常數，不來自外部輸入。

**在真實行情庫複本上的驗證**：

| 項目 | 結果 |
|---|---|
| 版本 | 0 → 2，耗時 48 ms |
| 資料 | 遷移前後 54,410 筆逐列相同（`SELECT * … ORDER BY stock_id, date` 的 SHA-256 一致） |
| 完整性 | `PRAGMA integrity_check` 為 `ok` |
| 再執行一次 | 版本 2 → 2，0.26 ms，資料不變 |

測試涵蓋：依舊版寫法建立的資料庫升級後資料不變、重複執行、寫入時自動升級、新資料庫直接是最新版本、某一步失敗時整個版本回滾、較新版本被拒絕、只查版本不會建立檔案，以及 CLI 的 `--check` 不修改資料庫。

## 公司行動表

第 3 版新增 `corporate_actions`，保存股票池的除權息、減資與變更面額（[data/corporate_actions.py](../src/tw_stock_backtest/data/corporate_actions.py)）：

- 主鍵 `(stock_id, effective_date, kind)`；`kind` 以 CHECK 限定為 `dividend`、`capital_reduction`、`par_change`。生效日是除權息日，減資與變更面額則是恢復買賣日。
- 官方公告的前收與參考價、每股現金股利、每千股配股或換發股數、退還股款都以字串保存 Decimal，和行情一致；SQL 只用來篩選與排序。
- `needs_review`、`review_notes` 記錄解析時發現的疑點，例如依公告數值反推的參考價對不上、減資表注意事項中無法對應到特定一次減資的併案股利。待確認的列只占少數，用部分索引 `idx_corporate_actions_review` 查詢。
- `sources`（https 網址）與 `raw`（原始回應列）讓每一筆都能回溯到官方資料。

目前只保存資料，指標、回測與報表都還沒有使用這張表。

## 在資料庫端完成的資料品質檢查

[data_status.py](../src/tw_stock_backtest/data_status.py) 原本把全部行情日期讀進 Python，再用 `bisect` 計算落後幾個行情日，並在 Python 中比對每檔最新日。現在改由 SQL 完成。專案沒有交易日曆，「行情日」指本機行情庫中出現過的日期。

### 總覽：最新日、股票數、停在較舊日期的股票

```sql
WITH latest AS (
    SELECT stock_id, MAX(date) AS last_date
    FROM stock_prices
    GROUP BY stock_id
),
newest AS (
    SELECT MAX(date) AS day FROM stock_prices
)
SELECT
    newest.day,
    COUNT(latest.stock_id),
    COUNT(CASE WHEN latest.last_date < newest.day THEN 1 END),
    MIN(CASE WHEN latest.last_date < newest.day THEN latest.last_date END)
FROM newest LEFT JOIN latest
```

### 某日之後還有幾個行情日

```sql
SELECT COUNT(DISTINCT date) FROM stock_prices WHERE date > ?
```

有日期索引時是 `SEARCH`。工作台在同一次請求內對相同日期只查一次。

### 各股品質：筆數、起訖日、缺價筆數、落後行情日數

```sql
WITH days AS (
    SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS day_number, COUNT(*) OVER () AS total
    FROM (SELECT DISTINCT date FROM stock_prices)
),
per_stock AS (
    SELECT stock_id, COUNT(*) AS row_count, MIN(date) AS first_date, MAX(date) AS last_date,
           SUM(close IS NULL) AS missing_close
    FROM stock_prices
    GROUP BY stock_id
)
SELECT s.stock_id, s.row_count, s.first_date, s.last_date, s.missing_close,
       d.total - d.day_number AS market_lag
FROM per_stock AS s
JOIN days AS d ON d.date = s.last_date
ORDER BY s.stock_id
```

`close IS NULL` 是這份 SQL 唯一碰到價格欄位的地方，而且只判斷是否為 NULL。

### 行情缺口（`LAG` 視窗函式）

替每個行情日編號，再用 `LAG` 取同一檔前一筆的日期與編號。兩筆之間編號差大於 1，表示其他股票有行情、這檔卻沒有列；日曆日間隔超過門檻，則用來找出全庫都沒有資料的長期空白（例如曾經整段沒下載的月份）。

```sql
WITH days AS (
    SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS day_number
    FROM (SELECT DISTINCT date FROM stock_prices)
),
ordered AS (
    SELECT p.stock_id, p.date, d.day_number,
           LAG(p.date) OVER per_stock AS previous_date,
           LAG(d.day_number) OVER per_stock AS previous_number
    FROM stock_prices AS p
    JOIN days AS d ON d.date = p.date
    WHERE (:stock_id IS NULL OR p.stock_id = :stock_id)
    WINDOW per_stock AS (PARTITION BY p.stock_id ORDER BY p.date)
)
SELECT stock_id, previous_date, date,
       day_number - previous_number - 1 AS missing_market_days,
       CAST(julianday(date) - julianday(previous_date) AS INTEGER) AS calendar_days
FROM ordered
WHERE previous_date IS NOT NULL
  AND (day_number - previous_number > 1
       OR julianday(date) - julianday(previous_date) > :calendar_days)
ORDER BY stock_id, previous_date
```

`julianday` 只用在兩個日期相減，不涉及價格。預設門檻 20 個日曆日，農曆年（約 12 天）不會被當成缺口。

這些查詢用在 `inspect_db`：

```bash
.venv/bin/python -m tw_stock_backtest.cli.inspect_db --config config.toml --gaps
```

### 與原本 Python 結果一致

[test_data_quality_sql.py](../tests/test_data_quality_sql.py) 把改寫前的 Python 實作原樣保留為參考答案，用 12 組固定亂數種子產生的合成行情庫逐項比對（總覽、行情日清單、每個日期的落後數、各股品質、兩種門檻的缺口、單檔缺口），要求完全相同；另有一項測試確認這些合成資料真的包含缺價、落後、缺口與長期空白。

在真實行情庫的兩份複本（遷移前、遷移後）上，同樣六項比對全部一致。時間（取多次執行的最小值，遷移後的複本）：

| 項目 | 原本 Python | SQL |
|---|---|---|
| 總覽 | 6.3 ms | 4.1 ms |
| 單一日期的落後行情日數 | 6.4 ms（每次請求先讀出全部日期） | 0.22 ms |
| 各股品質 | 43.8 ms | 13.3 ms |
| 行情缺口 | 51.4 ms | 51.8 ms |

缺口查詢的時間與 Python 相當：兩者都要走過全部 54,410 筆。改用 SQL 的好處是在資料庫端完成，不必把每一列搬進 Python。

### 真實資料中找到的狀況（2026-10-14 的複本）

- 37 檔共 111 筆收盤價為 NULL；最多的是 1538（50 筆中 30 筆）。
- 4 檔停在較舊日期：6768 落後 10 個行情日、4155 落後 5 個、4906 與 6196 各 1 個（另外 1589 在行情庫中沒有任何列，不在這 1,053 檔內）。
- 10 檔有 6～7 個行情日整列不存在（其他股票當天都有行情），例如 2601 的 2026-09-22 → 2026-10-05。可能是停牌或下載缺漏，需要逐一確認，不能自行認定為停牌。

## 限制

- 版本遷移只套用在行情庫；補充資料庫與自選清單資料庫仍以 `CREATE TABLE IF NOT EXISTS` 建立，沒有版本號。
- 遷移不執行 `ANALYZE`。實測執行後「某日之後的行情日數」還能再快一個數量級，但統計資料會隨每日更新過時，需要另外安排何時更新，這次沒有做。
- 「行情日」以本機資料推算，沒有獨立交易日曆；全庫都缺的日子只能靠日曆日門檻發現。
