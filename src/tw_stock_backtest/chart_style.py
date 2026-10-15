"""回測圖表的配色與文字。

中文標籤需要系統上有中文字型；CI、未裝中文字型的 Linux 或字型不齊的環境，
改用英文標籤。判斷時實際檢查字型是否含有要畫的每一個字，避免出現方框。
"""

from functools import lru_cache

# 與網頁設計變數一致。紅綠保留給漲跌，資產曲線不使用紅綠。
STRATEGY_COLORS = ("#3a4ac6", "#0c7a85", "#a56200", "#7b4fc9", "#566072")
BENCHMARK_COLOR = "#8490a1"
INK = "#1b2230"
MUTED = "#566072"
GRID = "#dde1e8"

# 依常見程度排列：Windows、macOS、Linux 的繁體中文字型。
CJK_FAMILIES = (
    "Microsoft JhengHei",
    "PingFang TC",
    "Noto Sans CJK TC",
    "Noto Sans TC",
    "Source Han Sans TC",
    "Heiti TC",
    "Noto Sans CJK JP",
    "WenQuanYi Zen Hei",
)

TEXT = {
    "zh": {
        "portfolio_title": "多股票回測與買進持有基準",
        "comparison_title": "策略比較：資產與回撤",
        "strategy": "策略",
        "benchmark": "買進持有",
        "initial": "初始資金",
        "return": "報酬",
        "drawdown_label": "最大回撤",
        "equity_axis": "總資產（元）",
        "drawdown_axis": "回撤",
        "date_axis": "日期",
        "period": "{start} 至 {end}",
        "fees": "以每日收盤計算總資產，已計入設定的手續費與交易稅。",
        "slippage": "單邊滑價 {rate}。",
        "no_slippage": "未計滑價。",
        "limits": "未調整除權息與拆股；期末不強制平倉；策略與基準的資金配置不同。",
    },
    "en": {
        "portfolio_title": "Portfolio backtest vs buy-and-hold benchmark",
        "comparison_title": "Strategy comparison: equity and drawdown",
        "strategy": "Strategy",
        "benchmark": "Buy and hold",
        "initial": "Initial capital",
        "return": "Return",
        "drawdown_label": "Max drawdown",
        "equity_axis": "Total equity (TWD)",
        "drawdown_axis": "Drawdown",
        "date_axis": "Date",
        "period": "{start} to {end}",
        "fees": "Daily closing equity; configured fees and taxes included.",
        "slippage": "One-way slippage {rate}.",
        "no_slippage": "No slippage.",
        "limits": "Not adjusted for dividends or splits; no forced liquidation; allocations differ.",
    },
}


def _font_path(family):
    from matplotlib import font_manager

    try:
        return font_manager.findfont(
            font_manager.FontProperties(family=family), fallback_to_default=False
        )
    except ValueError:
        return None


@lru_cache(maxsize=None)
def _glyphs(path):
    from matplotlib.ft2font import FT2Font

    return FT2Font(path)


def renders(path, text):
    """字型檔是否含有 text 的每一個字（空白除外）。"""
    font = _glyphs(path)
    return all(font.get_char_index(ord(char)) for char in text if not char.isspace())


@lru_cache(maxsize=None)
def cjk_font():
    """回傳 (字型名稱, 檔案路徑)；找不到能畫完整中文標籤的字型時回傳 None。"""
    sample = "".join(value for value in TEXT["zh"].values())
    for family in CJK_FAMILIES:
        path = _font_path(family)
        if path and renders(path, sample):
            return family, path
    return None


def style(font=None):
    """回傳 (語言, rc 設定, 實際使用的字型檔)。font 未指定時自動偵測；傳入 False 強制使用英文。"""
    found = cjk_font() if font is None else font or None
    rc = {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": GRID,
        "axes.labelcolor": MUTED,
        "axes.titlecolor": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "legend.frameon": False,
        "font.size": 10,
    }
    if found:
        rc["font.family"] = [found[0], "DejaVu Sans"]
        # 部分中文字型沒有數學負號，改用一般連字號。
        rc["axes.unicode_minus"] = False
        return "zh", rc, found[1]
    from matplotlib import font_manager

    return "en", rc, font_manager.findfont("DejaVu Sans")


def label(text, fallback, font_path):
    """字型畫得出 text 就用它，否則用 fallback（例如策略名稱換成策略 ID）。"""
    return text if text and renders(font_path, text) else fallback
