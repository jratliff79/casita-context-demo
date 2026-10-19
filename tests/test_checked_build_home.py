"""Cargo home must have the same meaning before and after changing build cwd."""
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_home',ROOT/'ci/build-relay-native.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class CargoHome(unittest.TestCase):
    def test_relative_or_empty_cargo_home_cannot_redirect_configuration_lookup(self):
        for value in ['relative-home','']:
            with self.subTest(value=value),self.assertRaises(ValueError):builder.validate_environment({'CARGO_HOME':value})
    def test_absolute_cargo_home_is_supported(self):
        builder.validate_environment({'CARGO_HOME':str(ROOT/'output/synthetic-cargo-home')})

if __name__=='__main__':unittest.main()
