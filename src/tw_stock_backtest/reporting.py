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
    strategy=None,
):
    """將本次回測輸出到新的獨立資料夾，不覆寫舊報表。"""
    from tw_stock_backtest.strategies import snapshot, from_settings
    effective_strategy = snapshot(strategy) if strategy is not None else (
        snapshot(from_settings(settings, result.get('ranking_method', 'rules')))
        if 'factors' in settings and 'screening' in settings else None)
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
            "strategy_snapshot": effective_strategy,
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

class ResearchReport:
    """執行中目錄保留 .incomplete；全部檔案完成後才原子改名。"""
    def __init__(self, output_root):
        root = Path(output_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid4().hex[:8]
        self.run_id = name
        self.path = root / (name + ".incomplete")
        self.final_path = root / name
        self.path.mkdir()

    def write_json(self, name, document):
        from tw_stock_backtest.cli.download_universe import save_progress
        # 重用既有原子 JSON 寫入；先套用 Decimal/date/Path 序列化。
        safe = json.loads(json.dumps(document, default=_json_default,
                                     ensure_ascii=False, allow_nan=False))
        save_progress(safe, self.path / name)

    def finish(self, summary):
        summary = dict(summary)
        rankings = summary.pop("rankings", [])
        self.write_json("rankings.json", rankings)
        with (self.path / "rankings.csv").open("w", encoding="utf-8-sig", newline="") as file:
            fields = ["rank", "stock_id", "date", "score", "momentum", "trend", "volatility"]
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for row in rankings:
                writer.writerow({**{k: row[k] for k in fields[:4]}, **{k: row.get("factors", {}).get(k, "") for k in fields[4:]}})
        self.write_json("summary.json", {**summary, "report_complete": True})
        self.path.rename(self.final_path)
        return self.final_path
