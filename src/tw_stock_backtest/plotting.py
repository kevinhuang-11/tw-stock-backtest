import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from tw_stock_backtest.backtesting.metrics import (
    summarize_performance,
)


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
        drawdowns.append(
            equity / running_peak - Decimal("1")
        )

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

    strategy = prepare_series(
        document["strategy"]["result"]
    )
    benchmark = prepare_series(
        document["benchmark"]["result"]
    )

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

    fig, (equity_ax, drawdown_ax) = plt.subplots(
        2,
        1,
        figsize=(12, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [2, 1]},
    )

    try:
        for label, series, color in (
            ("Strategy", strategy, "#2563eb"),
            ("Buy and hold", benchmark, "#d97706"),
        ):
            performance = series["performance"]

            # Decimal 保留到繪圖邊界才轉成 float。
            equities = [
                float(value)
                for value in series["equities"]
            ]
            drawdowns = [
                float(value)
                for value in series["drawdowns"]
            ]

            equity_ax.plot(
                series["dates"],
                equities,
                color=color,
                linewidth=2,
                label=(
                    f"{label} | Return "
                    f"{performance['total_return']:.2%}"
                ),
            )

            drawdown_ax.plot(
                series["dates"],
                drawdowns,
                color=color,
                linewidth=1.8,
                label=(
                    f"{label} | Max drawdown "
                    f"{performance['max_drawdown']:.2%}"
                ),
            )

        equity_ax.axhline(
            float(strategy["initial_cash"]),
            color="#64748b",
            linestyle="--",
            linewidth=1,
            label="Initial capital",
        )

        first_date = strategy["dates"][0]
        last_date = strategy["dates"][-1]

        equity_ax.set_title(
            "Portfolio backtest vs buy-and-hold benchmark\n"
            f"{first_date} to {last_date}",
            loc="left",
            fontsize=14,
        )
        equity_ax.set_ylabel("Total equity (TWD)")
        equity_ax.yaxis.set_major_formatter(
            StrMethodFormatter("{x:,.0f}")
        )

        drawdown_ax.set_ylabel("Drawdown")
        drawdown_ax.set_xlabel("Date")
        drawdown_ax.yaxis.set_major_formatter(
            PercentFormatter(xmax=1)
        )
        drawdown_ax.axhline(
            0,
            color="#64748b",
            linewidth=1,
        )

        locator = mdates.AutoDateLocator(
            minticks=4,
            maxticks=8,
        )
        drawdown_ax.xaxis.set_major_locator(locator)
        drawdown_ax.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(locator)
        )

        for ax in (equity_ax, drawdown_ax):
            ax.grid(True, alpha=0.25)
            ax.legend(loc="best", fontsize=9)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

        fig.text(
            0.5,
            0.025,
            "Daily closing equity; configured fees and taxes included.\n"
            "Excludes slippage, dividends and splits; "
            "no forced liquidation. Allocations differ.",
            ha="center",
            fontsize=9,
            color="#475569",
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