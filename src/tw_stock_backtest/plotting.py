import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from tw_stock_backtest.backtesting.metrics import (
    summarize_performance,
)
from tw_stock_backtest.prices import report_price_mode


def prepare_series(result):
    """將 JSON 中的金額還原為 Decimal，計算逐日回撤。"""
    initial_cash = Decimal(result["initial_cash"])

    equity_curve = [
        {
            **point,
            "equity": Decimal(point["equity"]),
        }
        for point in result["equity_curve"]
    ]

    # 沿用既有驗證與績效計算，避免只相信報表中的摘要。
    performance = summarize_performance(
        equity_curve,
        initial_cash,
    )

    dates = []
    equities = []
    drawdowns = []

    running_peak = initial_cash

    for point in equity_curve:
        equity = point["equity"]
        running_peak = max(running_peak, equity)

        dates.append(date.fromisoformat(point["date"]))
        equities.append(equity)

        # 圖表用負數表示回撤，例如 -0.10 表示下跌 10%。
        drawdowns.append(equity / running_peak - Decimal("1"))

    return {
        "initial_cash": initial_cash,
        "dates": dates,
        "equities": equities,
        "drawdowns": drawdowns,
        "performance": performance,
    }


def plot_portfolio_report(report_dir):
    """讀取既有報表，將比較圖存為 performance.png。"""
    report_dir = Path(report_dir).expanduser().resolve()
    results_path = report_dir / "results.json"

    with results_path.open(encoding="utf-8") as file:
        document = json.load(file)

    if document.get("schema_version") != 1:
        raise ValueError("不支援的報表格式版本")

    settings_path = report_dir / "settings.json"
    configuration = (
        json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    )
    # 舊報表沒有價格口徑的紀錄，視為原始價格。
    price_mode = report_price_mode(document["strategy"]["result"], configuration.get("settings"))

    strategy = prepare_series(document["strategy"]["result"])
    benchmark = prepare_series(document["benchmark"]["result"])

    if strategy["dates"] != benchmark["dates"]:
        raise ValueError("策略與基準的日期不一致")

    if strategy["initial_cash"] != benchmark["initial_cash"]:
        raise ValueError("策略與基準的初始資金不一致")

    # 僅在實際畫圖時載入 Matplotlib。
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter, StrMethodFormatter

    from tw_stock_backtest.chart_style import (
        BENCHMARK_COLOR,
        MUTED,
        STRATEGY_COLORS,
        TEXT,
        limits_text,
        style,
    )

    # 有中文字型時用中文標籤，否則改用英文，避免出現方框。
    language, rc, _ = style()
    text = TEXT[language]
    slippage = Decimal(str(document["strategy"]["result"].get("slippage_rate") or 0))

    with plt.rc_context(rc):
        fig, (equity_ax, drawdown_ax) = plt.subplots(
            2,
            1,
            figsize=(12, 8),
            sharex=True,
            gridspec_kw={"height_ratios": [2, 1]},
        )

    try:
        with plt.rc_context(rc):
            for label, series, color, width in (
                (text["strategy"], strategy, STRATEGY_COLORS[0], 2.2),
                (text["benchmark"], benchmark, BENCHMARK_COLOR, 1.8),
            ):
                performance = series["performance"]

                # Decimal 保留到繪圖邊界才轉成 float。
                equities = [float(value) for value in series["equities"]]
                drawdowns = [float(value) for value in series["drawdowns"]]

                equity_ax.plot(
                    series["dates"],
                    equities,
                    color=color,
                    linewidth=width,
                    label=(f"{label} | {text['return']} {performance['total_return']:.2%}"),
                )

                drawdown_ax.plot(
                    series["dates"],
                    drawdowns,
                    color=color,
                    linewidth=width - 0.4,
                    label=(f"{label} | {text['drawdown_label']} {performance['max_drawdown']:.2%}"),
                )

            equity_ax.axhline(
                float(strategy["initial_cash"]),
                color=MUTED,
                linestyle="--",
                linewidth=1,
                label=text["initial"],
            )

            first_date = strategy["dates"][0]
            last_date = strategy["dates"][-1]

            equity_ax.set_title(
                text["portfolio_title"]
                + "\n"
                + text["period"].format(start=first_date, end=last_date),
                loc="left",
                fontsize=14,
            )
            equity_ax.set_ylabel(text["equity_axis"])
            equity_ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))

            drawdown_ax.set_ylabel(text["drawdown_axis"])
            drawdown_ax.set_xlabel(text["date_axis"])
            drawdown_ax.yaxis.set_major_formatter(PercentFormatter(xmax=1))
            drawdown_ax.axhline(
                0,
                color=MUTED,
                linewidth=1,
            )

            locator = mdates.AutoDateLocator(
                minticks=4,
                maxticks=8,
            )
            drawdown_ax.xaxis.set_major_locator(locator)
            drawdown_ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

            for ax in (equity_ax, drawdown_ax):
                ax.legend(loc="best", fontsize=9)

            # 註記依實際設定說明是否計入滑價。
            # 中文句子之間不加空白；英文句子之間空一格。
            costs = (
                text["fees"]
                + ("" if language == "zh" else " ")
                + (
                    text["slippage"].format(rate=f"{slippage:.2%}")
                    if slippage
                    else text["no_slippage"]
                )
            )
            fig.text(
                0.5,
                0.025,
                costs + "\n" + limits_text(text, price_mode),
                ha="center",
                fontsize=9,
                color=MUTED,
            )

            fig.tight_layout(rect=(0, 0.08, 1, 1))

            output_path = report_dir / "performance.png"

            fig.savefig(
                output_path,
                dpi=180,
                facecolor="white",
            )

    finally:
        plt.close(fig)

    return output_path
