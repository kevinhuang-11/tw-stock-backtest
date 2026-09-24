import argparse

from tw_stock_backtest.plotting import plot_portfolio_report


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="讀取回測報表，繪製資產與回撤比較圖"
    )

    parser.add_argument(
        "--report-dir",
        required=True,
        help="包含 results.json 的報表資料夾",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        output_path = plot_portfolio_report(
            args.report_dir
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        ArithmeticError,
        OSError,
    ) as error:
        print(f"繪圖失敗：{error}")
        return 1

    print(f"圖表已儲存：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())