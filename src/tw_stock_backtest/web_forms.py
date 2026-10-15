"""Validated adapters between human-facing forms and existing execution formats."""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from uuid import uuid4

from tw_stock_backtest.strategies import FACTOR_FIELDS, RULE_FIELDS, validate_strategy
from tw_stock_backtest.strategy_comparison import validate_periods


class FormErrors(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("表單尚有錯誤")


def decimal_value(value, field, minimum=Decimal(0), maximum=None):
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed < minimum or (maximum is not None and parsed > maximum):
            raise ValueError()
        return parsed
    except (InvalidOperation, ValueError, TypeError):
        raise FormErrors({field: "請輸入有效範圍內的有限數字"})


def percent_ratio(value):
    number = decimal_value(value, "slippage_percent", maximum=Decimal(100))
    if number >= 100:
        raise FormErrors({"slippage_percent": "滑價須小於 100%"})
    with localcontext() as context:
        context.prec = max(28, len(number.as_tuple().digits) + 4)
        return str(number.scaleb(-2))


def ratio_percent(value):
    number = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(28, len(number.as_tuple().digits) + 4)
        return format(number.scaleb(2), "f")


def job_form(source):
    form = source.to_dict() if hasattr(source, "to_dict") else dict(source)
    if hasattr(source, "getlist") and len(source.getlist("strategies")) > 1:
        form["strategies"] = " ".join(source.getlist("strategies"))
    if form.get("stocks"):
        codes = re.split(r"[\s,，;；]+", form["stocks"].strip())
        if (
            not 1 <= len(codes) <= 10
            or len(set(codes)) != len(codes)
            or any(not re.fullmatch(r"[0-9]{4}", x) for x in codes)
        ):
            raise FormErrors({"stocks": "請選擇 1～10 檔不重複的四位數股票代號"})
        form["stocks"] = " ".join(codes)
    for field in ("start", "end", "as_of"):
        if form.get(field):
            try:
                if date.fromisoformat(form[field]).isoformat() != form[field]:
                    raise ValueError()
            except (TypeError, ValueError):
                raise FormErrors({field: "請選擇有效日期"})
    if form.get("start") and form.get("end") and form["start"] > form["end"]:
        raise FormErrors({"end": "結束日期不可早於開始日期"})
    if form.get("slippage_percent") is not None:
        if form.get("slippage") is not None:
            raise FormErrors({"slippage_percent": "不能同時提供百分比與內部滑價比例"})
        form["slippage"] = percent_ratio(form["slippage_percent"])
        del form["slippage_percent"]
    for field in ("cash", "quantity", "max_positions"):
        if form.get(field):
            number = decimal_value(form[field], field)
            if number <= 0 or (
                field != "cash"
                and (
                    number != number.to_integral_value() or not re.fullmatch(r"[0-9]+", form[field])
                )
            ):
                raise FormErrors({field: "需大於 0；股數與持股上限需為整數"})
    if form.get("sizing") not in (None, "", "fixed_quantity", "fixed_budget"):
        raise FormErrors({"sizing": "請選擇支援的配置模式"})
    if form.get("ranking") not in (None, "", "factors", "rules"):
        raise FormErrors({"ranking": "請選擇支援的排名模式"})
    if form.get("kind") in ("comparison", "period_comparison"):
        selected = form.get("strategies", "").split()
        if not 2 <= len(selected) <= 5 or len(set(selected)) != len(selected):
            raise FormErrors({"strategies": "請選擇 2～5 份不同策略"})
    if form.get("kind") == "period_comparison":
        try:
            validate_periods(json.loads(form.get("periods", "[]")))
        except (ValueError, TypeError, KeyError):
            raise FormErrors({"periods": "請檢查期間日期、用途及重複識別；最多 12 個期間"})
    return form


def strategy_form(source):
    if source.get("action") == "import":
        try:
            value = json.loads(source.get("strategy_json", ""), parse_float=str)
            return validate_strategy(value)
        except (ValueError, KeyError, TypeError):
            raise FormErrors(
                {
                    "strategy_json": "策略 JSON 不符合既有 schema：請檢查版本、全部欄位、數值與條件；未忽略任何欄位"
                }
            )
    errors = {}
    for key, limit in (("name", 100), ("description", 1000)):
        if not source.get(key, "").strip() or len(source.get(key, "")) > limit:
            errors[key] = f"必填，最多 {limit} 字"
    if source.get("ranking_mode") not in ("rules", "factors"):
        errors["ranking_mode"] = "請選擇排名模式"
    value = {
        "schema_version": 1,
        "id": source.get("id") or "strategy_" + uuid4().hex[:16],
        "name": source.get("name", ""),
        "description": source.get("description", ""),
        "ranking_mode": source.get("ranking_mode"),
    }
    for group, keys in (("factors", FACTOR_FIELDS), ("screening", RULE_FIELDS)):
        value[group] = {}
        for key in keys:
            field = group + "." + key
            raw = source.get(field, "")
            try:
                decimal_value(raw, field)
                if key.endswith("_weight") or key == "min_volume_ratio":
                    value[group][key] = raw
                else:
                    if not re.fullmatch(r"[0-9]+", str(raw)) or not 1 <= int(raw) <= 1000:
                        raise FormErrors({field: "需為 1～1000 的整數"})
                    value[group][key] = int(raw)
            except FormErrors as error:
                errors.update(error.errors)
        if (
            "short_window" in value[group]
            and "long_window" in value[group]
            and value[group]["short_window"] >= value[group]["long_window"]
        ):
            errors[group + ".long_window"] = "長期窗口必須大於短期窗口"
    if all(k in value["factors"] for k in ("momentum_weight", "trend_weight", "volatility_weight")):
        if not any(
            Decimal(value["factors"][k]) > 0
            for k in ("momentum_weight", "trend_weight", "volatility_weight")
        ):
            errors["factors.momentum_weight"] = "三個因子權重不可全為零"
    try:
        value["conditions"] = json.loads(source.get("conditions", "[]"), parse_float=str)
    except ValueError:
        errors["conditions"] = "條件格式無效"
    if errors:
        raise FormErrors(errors)
    try:
        return validate_strategy(value)
    except ValueError as error:
        # Existing validation messages are fixed schema descriptions, never raw config.
        raise FormErrors({"conditions": str(error)})
