import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tw_stock_backtest.config import apply_overrides, load_config
from tw_stock_backtest.config import validate_settings

TEST_CONFIG = """
[universe]
stocks = ["2330", "2317"]

[screening]
short_window = 5
long_window = 20
volume_window = 20
min_volume_ratio = "1"
momentum_window = 20
top_n = 3

[strategy]
short_window = 5
long_window = 10

[backtest]
initial_cash = "1000000"
quantity = 100
max_positions = 2

[costs]
commission_rate = "0.001425"
commission_discount = "1"
minimum_commission = "20"
sell_tax_rate = "0.003"
commission_rounding = "ROUND_DOWN"
tax_rounding = "ROUND_DOWN"

[download]
timeout_seconds = 30
request_interval_seconds = 3
max_attempts = 3
retry_wait_seconds = 3

[storage]
database_path = "data/stocks.db"

[factors]
short_window = 5
long_window = 20
momentum_window = 20
volatility_window = 20
top_n = 3
momentum_weight = "1"
trend_weight = "1"
volatility_weight = "1"
"""


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.config_path = Path(self.temp_dir.name) / "config.toml"
        self.write_config(TEST_CONFIG)

    def write_config(self, text):
        self.config_path.write_text(text, encoding="utf-8")

    def test_load_and_convert_types(self):
        settings = load_config(self.config_path)

        self.assertEqual(
            settings["universe"]["stocks"],
            ["2330", "2317"],
        )
        self.assertEqual(
            settings["screening"]["short_window"],
            5,
        )
        self.assertIsInstance(
            settings["costs"]["commission_rate"],
            Decimal,
        )
        self.assertEqual(
            settings["costs"]["commission_rate"],
            Decimal("0.001425"),
        )

    def test_database_path_is_relative_to_config(self):
        settings = load_config(self.config_path)

        expected = (
            self.config_path.parent / "data" / "stocks.db"
        ).resolve()

        self.assertEqual(
            settings["storage"]["database_path"],
            expected,
        )

        # 讀取設定本身不應建立資料庫。
        self.assertFalse(expected.exists())

    def test_missing_field_is_rejected(self):
        self.write_config(
            TEST_CONFIG.replace("top_n = 3", "")
        )

        with self.assertRaises(ValueError):
            load_config(self.config_path)

    def test_unknown_field_is_rejected(self):
        self.write_config(
            TEST_CONFIG.replace("top_n = 3", "top_count = 3")
        )

        with self.assertRaises(ValueError):
            load_config(self.config_path)

    def test_invalid_values_are_rejected(self):
        cases = (
            ("short_window = 5", "short_window = 0"),
            ("long_window = 20", "long_window = 5"),
            ("quantity = 100", "quantity = true"),
            ('initial_cash = "1000000"', 'initial_cash = "0"'),
            ('min_volume_ratio = "1"', 'min_volume_ratio = "NaN"'),
            ('commission_rate = "0.001425"', "commission_rate = 0.001425"),
            ('sell_tax_rate = "0.003"', 'sell_tax_rate = "2"'),
            ('minimum_commission = "20"', 'minimum_commission = "1.5"'),
            ('tax_rounding = "ROUND_DOWN"', 'tax_rounding = "UNKNOWN"'),
            ("timeout_seconds = 30", "timeout_seconds = 0"),
            ("request_interval_seconds = 3", "request_interval_seconds = -1"),
        )

        for original, replacement in cases:
            with self.subTest(replacement=replacement):
                self.write_config(
                    TEST_CONFIG.replace(original, replacement)
                )

                with self.assertRaises(ValueError):
                    load_config(self.config_path)

    def test_override_preserves_zero_and_original_settings(self):
        settings = load_config(self.config_path)

        updated = apply_overrides(
            settings,
            {
                "costs": {
                    "minimum_commission": "0",
                    "sell_tax_rate": "0",
                },
                "screening": {
                    "top_n": 5,
                },
            },
        )

        self.assertEqual(
            updated["costs"]["minimum_commission"],
            Decimal("0"),
        )
        self.assertEqual(
            updated["costs"]["sell_tax_rate"],
            Decimal("0"),
        )
        self.assertEqual(updated["screening"]["top_n"], 5)

        self.assertEqual(
            settings["costs"]["minimum_commission"],
            Decimal("20"),
        )
        self.assertEqual(settings["screening"]["top_n"], 3)

    def test_none_does_not_override_config(self):
        settings = load_config(self.config_path)

        updated = apply_overrides(
            settings,
            {"screening": {"top_n": None}},
        )

        self.assertEqual(updated["screening"]["top_n"], 3)

    def test_invalid_override_is_rejected(self):
        settings = load_config(self.config_path)

        with self.assertRaises(ValueError):
            apply_overrides(
                settings,
                {"strategy": {"short_window": 10}},
            )

    def test_missing_file_is_reported(self):
        missing_path = self.config_path.parent / "missing.toml"

        with self.assertRaises(FileNotFoundError):
            load_config(missing_path)

    def test_legacy_config_defaults_to_fixed_quantity(self):
        settings = load_config(self.config_path)

        # 模擬沒有新欄位的舊設定。
        settings["backtest"].pop("sizing_mode", None)

        validated = validate_settings(settings)

        self.assertEqual(
            validated["backtest"]["sizing_mode"],
            "fixed_quantity",
        )

        # 驗證過程不應修改傳入的設定。
        self.assertNotIn("sizing_mode", settings["backtest"])

    def test_load_fixed_budget_from_file(self):
        text = self.config_path.read_text(encoding="utf-8")

        # 在既有 backtest 區段插入新設定。
        text = text.replace(
            "[backtest]",
            '[backtest]\nsizing_mode = "fixed_budget"',
            1,
        )
        self.config_path.write_text(text, encoding="utf-8")

        settings = load_config(self.config_path)

        self.assertEqual(
            settings["backtest"]["sizing_mode"],
            "fixed_budget",
        )

    def test_sizing_override_and_none_preserve_settings(self):
        settings = load_config(self.config_path)

        fixed = apply_overrides(
            settings,
            {"backtest": {"sizing_mode": "fixed_quantity"}},
        )
        budget = apply_overrides(
            fixed,
            {"backtest": {"sizing_mode": "fixed_budget"}},
        )
        unchanged = apply_overrides(
            budget,
            {"backtest": {"sizing_mode": None}},
        )

        self.assertEqual(
            fixed["backtest"]["sizing_mode"],
            "fixed_quantity",
        )
        self.assertEqual(
            budget["backtest"]["sizing_mode"],
            "fixed_budget",
        )
        self.assertEqual(
            unchanged["backtest"]["sizing_mode"],
            "fixed_budget",
        )

    def test_invalid_sizing_modes_are_rejected(self):
        settings = load_config(self.config_path)

        for value in ("unknown", "", 1, True, None):
            with self.subTest(value=value):
                # 直接驗證設定中的 None，應拒絕。
                # 命令列覆寫的 None 則代表「未指定」。
                candidate = load_config(self.config_path)
                candidate["backtest"]["sizing_mode"] = value

                with self.assertRaises(ValueError):
                    validate_settings(candidate)

        with self.assertRaises(ValueError):
            apply_overrides(
                settings,
                {"backtest": {"sizing_mode": "unknown"}},
            )
    def test_legacy_config_defaults_to_zero_slippage(self):
        settings = load_config(self.config_path)
        settings["backtest"].pop("slippage_rate", None)

        result = validate_settings(settings)

        self.assertEqual(
            result["backtest"]["slippage_rate"],
            Decimal("0"),
        )
        self.assertNotIn("slippage_rate", settings["backtest"])

    def test_load_slippage_from_file(self):
        text = self.config_path.read_text(encoding="utf-8")
        text = text.replace(
            "[backtest]",
            '[backtest]\nslippage_rate = "0.001"',
            1,
        )
        self.config_path.write_text(text, encoding="utf-8")

        settings = load_config(self.config_path)

        self.assertEqual(
            settings["backtest"]["slippage_rate"],
            Decimal("0.001"),
        )

    def test_slippage_override_preserves_zero_and_original(self):
        original = load_config(self.config_path)
        original_rate = original["backtest"]["slippage_rate"]

        changed = apply_overrides(
            original,
            {"backtest": {"slippage_rate": "0.001"}},
        )
        unchanged = apply_overrides(
            changed,
            {"backtest": {"slippage_rate": None}},
        )
        zero = apply_overrides(
            changed,
            {"backtest": {"slippage_rate": "0"}},
        )

        self.assertEqual(
            changed["backtest"]["slippage_rate"],
            Decimal("0.001"),
        )
        self.assertEqual(
            unchanged["backtest"]["slippage_rate"],
            Decimal("0.001"),
        )
        self.assertEqual(
            zero["backtest"]["slippage_rate"],
            Decimal("0"),
        )
        self.assertEqual(
            original["backtest"]["slippage_rate"],
            original_rate,
        )

    def test_invalid_slippage_settings(self):
        settings = load_config(self.config_path)

        for value in ("-0.001", "1", "NaN", "Infinity", 0.001, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    apply_overrides(
                        settings,
                        {"backtest": {"slippage_rate": value}},
                    )

if __name__ == "__main__":
    unittest.main()