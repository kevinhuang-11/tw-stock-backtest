import csv
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    raise TypeError(
        f"不支援的 JSON 資料型別：{type(value).__name__}"
    )


def export_portfolio_report(
    output_root,
    *,
    settings,
    start_text,
    end_text,
    records_by_stock,
    result,
    performance,
    benchmark,
    benchmark_performance,
):
    """將本次回測輸出到新的獨立資料夾，不覆寫舊報表。"""
    strategy_curve = result["equity_curve"]
    benchmark_curve = benchmark["equity_curve"]

    strategy_dates = [
        point["date"] for point in strategy_curve
    ]
    benchmark_dates = [
        point["date"] for point in benchmark_curve
    ]

    if strategy_dates != benchmark_dates:
        raise ValueError("策略與基準的資產日期不一致，無法匯出比較表")

    created_at = datetime.now(timezone.utc)

    documents = {
        "settings.json": {
            "schema_version": 1,
            "created_at_utc": created_at.isoformat(),
            "requested_start": start_text,
            "requested_end": end_text,
            "settings": settings,
        },
        "results.json": {
            "schema_version": 1,
            "strategy": {
                "result": result,
                "performance": performance,
            },
            "benchmark": {
                "result": benchmark,
                "performance": benchmark_performance,
            },
        },
        "input_data.json": {
            "schema_version": 1,
            "records_by_stock": records_by_stock,
        },
    }

    # 先確認所有 JSON 都能轉換，再建立輸出資料夾。
    encoded_documents = {
        filename: json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
            allow_nan=False,
        )
        for filename, document in documents.items()
    }

    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    run_name = (
        created_at.strftime("%Y%m%dT%H%M%S_%fZ")
        + "_"
        + uuid4().hex[:8]
    )

    report_dir = output_root / run_name
    report_dir.mkdir()

    for filename, text in encoded_documents.items():
        (report_dir / filename).write_text(
            text + "\n",
            encoding="utf-8",
        )

    trade_fields = [
        "source",
        "stock_id",
        "signal_date",
        "date",
        "action",
        "price",
        "quantity",
        "amount",
        "commission",
        "tax",
        "cash_change",
        "cash_after",
    ]

    with (report_dir / "trades.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=trade_fields,
        )
        writer.writeheader()

        for source, source_result in (
            ("strategy", result),
            ("benchmark", benchmark),
        ):
            for trade in source_result["trades"]:
                row = {
                    field: trade.get(field, "")
                    for field in trade_fields
                }
                row["source"] = source
                writer.writerow(row)

    equity_fields = [
        "date",
        "strategy_cash",
        "strategy_market_value",
        "strategy_equity",
        "benchmark_cash",
        "benchmark_market_value",
        "benchmark_equity",
    ]

    with (report_dir / "equity.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=equity_fields,
        )
        writer.writeheader()

        for strategy_point, benchmark_point in zip(
            strategy_curve,
            benchmark_curve,
        ):
            writer.writerow(
                {
                    "date": strategy_point["date"],
                    "strategy_cash": strategy_point["cash"],
                    "strategy_market_value": (
                        strategy_point["market_value"]
                    ),
                    "strategy_equity": strategy_point["equity"],
                    "benchmark_cash": benchmark_point["cash"],
                    "benchmark_market_value": (
                        benchmark_point["market_value"]
                    ),
                    "benchmark_equity": benchmark_point["equity"],
                }
            )

    return report_dir