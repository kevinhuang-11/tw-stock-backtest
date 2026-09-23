import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from config import apply_overrides, load_config


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

[storage]
database_path = "data/stocks.db"
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


if __name__ == "__main__":
    unittest.main()