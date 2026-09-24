from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"

# 各區段必須提供的欄位。
REQUIRED_FIELDS = {
    "universe": {
        "stocks",
    },
    "screening": {
        "short_window",
        "long_window",
        "volume_window",
        "min_volume_ratio",
        "momentum_window",
        "top_n",
    },
    "strategy": {
        "short_window",
        "long_window",
    },
    "backtest": {
        "initial_cash",
        "quantity",
        "max_positions",
    },
    "costs": {
        "commission_rate",
        "commission_discount",
        "minimum_commission",
        "sell_tax_rate",
        "commission_rounding",
        "tax_rounding",
    },
    "download": {
        "timeout_seconds",
        "request_interval_seconds",
        "max_attempts",
        "retry_wait_seconds",
    },
    "storage": {
        "database_path",
    },
    "factors": {
        "short_window",
        "long_window",
        "momentum_window",
        "volatility_window",
        "top_n",
        "momentum_weight",
        "trend_weight",
        "volatility_weight",
    },
}
# 選填欄位及其預設值。
OPTIONAL_DEFAULTS = {
    "backtest": {
        "sizing_mode": "fixed_quantity",
    },
}
DECIMAL_FIELDS = (
    ("screening", "min_volume_ratio"),
    ("backtest", "initial_cash"),
    ("costs", "commission_rate"),
    ("costs", "commission_discount"),
    ("costs", "minimum_commission"),
    ("costs", "sell_tax_rate"),
    ("factors", "momentum_weight"),
    ("factors", "trend_weight"),
    ("factors", "volatility_weight"),
)


POSITIVE_INTEGER_FIELDS = (
    ("screening", "short_window"),
    ("screening", "long_window"),
    ("screening", "volume_window"),
    ("screening", "momentum_window"),
    ("screening", "top_n"),
    ("strategy", "short_window"),
    ("strategy", "long_window"),
    ("backtest", "quantity"),
    ("backtest", "max_positions"),
    ("download", "max_attempts"),
    ("download", "retry_wait_seconds"),
    ("factors", "short_window"),
    ("factors", "long_window"),
    ("factors", "momentum_window"),
    ("factors", "volatility_window"),
    ("factors", "top_n"),
)


def _to_decimal(value, name):
    """將設定中的字串或整數轉成 Decimal，不接受浮點數。"""
    if isinstance(value, bool) or not isinstance(
        value, (str, int, Decimal)
    ):
        raise ValueError(
            f"{name} 必須使用字串或整數；小數請加引號"
        )

    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} 不是有效數值") from error

    if not result.is_finite():
        raise ValueError(f"{name} 必須是有限數值")

    return result


def validate_settings(settings):
    """檢查設定並回傳獨立副本，不修改傳入的字典。"""
    result = deepcopy(settings)

    unknown_sections = set(result) - set(REQUIRED_FIELDS)
    if unknown_sections:
        raise ValueError(
            f"未知設定區段：{', '.join(sorted(unknown_sections))}"
        )

    for section, required in REQUIRED_FIELDS.items():
        values = result.get(section)

        if not isinstance(values, dict):
            raise ValueError(f"缺少或無效的設定區段：{section}")

        missing = required - set(values)
        if missing:
            raise ValueError(
                f"{section} 缺少欄位：{', '.join(sorted(missing))}"
            )

        optional = OPTIONAL_DEFAULTS.get(section, {})
        allowed = required | set(optional)

        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                f"{section} 有未知欄位：{', '.join(sorted(unknown))}"
            )

        # 只在沒提供欄位時補預設值，不掩蓋無效值。
        for key, default in optional.items():
            values.setdefault(key, default)
    sizing_mode = result["backtest"]["sizing_mode"]

    if sizing_mode not in ("fixed_quantity", "fixed_budget"):
        raise ValueError(
            "backtest.sizing_mode 必須是 "
            "fixed_quantity 或 fixed_budget"
        )
    
    stocks = result["universe"]["stocks"]

    if (
        not isinstance(stocks, list)
        or not stocks
        or any(
            not isinstance(stock, str) or not stock.strip()
            for stock in stocks
        )
    ):
        raise ValueError("universe.stocks 必須是非空的股票代號字串清單")

    # 去掉前後空白，並保留原順序去除重複代號。
    result["universe"]["stocks"] = list(
        dict.fromkeys(stock.strip() for stock in stocks)
    )

    for section, key in POSITIVE_INTEGER_FIELDS:
        value = result[section][key]

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise ValueError(f"{section}.{key} 必須是正整數")

    for section in ("screening", "strategy", "factors"):
        if (
            result[section]["short_window"]
            >= result[section]["long_window"]
        ):
            raise ValueError(
                f"{section} 必須符合：短期期間 < 長期期間"
            )

    for section, key in DECIMAL_FIELDS:
        result[section][key] = _to_decimal(
            result[section][key],
            f"{section}.{key}",
        )
    factor_weights = [
        result["factors"][key]
        for key in (
            "momentum_weight",
            "trend_weight",
            "volatility_weight",
        )
    ]

    if any(weight < 0 for weight in factor_weights):
        raise ValueError("因子權重不可為負數")

    if sum(factor_weights, Decimal("0")) == 0:
        raise ValueError("因子權重不可全部為 0")

    if result["screening"]["min_volume_ratio"] < 0:
        raise ValueError("screening.min_volume_ratio 不可為負數")

    if result["backtest"]["initial_cash"] <= 0:
        raise ValueError("backtest.initial_cash 必須大於 0")

    costs = result["costs"]

    for key in (
        "commission_rate",
        "commission_discount",
        "sell_tax_rate",
    ):
        if not Decimal("0") <= costs[key] <= Decimal("1"):
            raise ValueError(f"costs.{key} 必須介於 0 與 1 之間")

    minimum = costs["minimum_commission"]
    if minimum < 0 or minimum != minimum.to_integral_value():
        raise ValueError("costs.minimum_commission 必須是非負整數金額")

    for key in ("commission_rounding", "tax_rounding"):
        if costs[key] not in ("ROUND_DOWN", "ROUND_HALF_UP"):
            raise ValueError(
                f"costs.{key} 只支援 ROUND_DOWN 或 ROUND_HALF_UP"
            )

    for key in ("timeout_seconds", "request_interval_seconds"):
        value = result["download"][key]

        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"download.{key} 必須是整數")

    if result["download"]["timeout_seconds"] <= 0:
        raise ValueError("download.timeout_seconds 必須大於 0")

    if result["download"]["request_interval_seconds"] < 0:
        raise ValueError("download.request_interval_seconds 不可為負數")

    db_path = result["storage"]["database_path"]
    if not isinstance(db_path, (str, Path)) or not str(db_path).strip():
        raise ValueError("storage.database_path 必須是非空路徑")

    return result


def load_config(config_path=DEFAULT_CONFIG_PATH):
    """讀取 TOML；資料庫相對路徑以設定檔所在目錄為基準。"""
    path = Path(config_path).expanduser().resolve()

    with path.open("rb") as file:
        settings = tomllib.load(file)

    settings = validate_settings(settings)

    db_path = Path(
        settings["storage"]["database_path"]
    ).expanduser()

    if not db_path.is_absolute():
        db_path = path.parent / db_path

    settings["storage"]["database_path"] = db_path.resolve()

    return settings


def apply_overrides(settings, overrides):
    """只套用非 None 的覆寫值，並重新驗證完整設定。"""
    result = deepcopy(settings)

    for section, values in overrides.items():
        if section not in REQUIRED_FIELDS:
            raise ValueError(f"未知設定區段：{section}")

        allowed = (
            REQUIRED_FIELDS[section]
            | set(OPTIONAL_DEFAULTS.get(section, {}))
        )

        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"未知設定欄位：{section}.{key}")
            if value is not None:
                result[section][key] = value

    return validate_settings(result)