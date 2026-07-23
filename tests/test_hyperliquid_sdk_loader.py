from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from kairospy.infrastructure.configuration import KairosProjectConfig
from kairospy.integrations.connectors.hyperliquid.sdk_loader import (
    HyperliquidSdkUnavailable,
    load_hyperliquid_sdk_binding,
)
from kairospy.integrations.config import resolve_hyperliquid_trading_credentials


class HyperliquidSdkLoaderTests(unittest.TestCase):
    def test_resolves_hyperliquid_trading_credentials_without_exposing_secret(self) -> None:
        config = _config()

        credentials = resolve_hyperliquid_trading_credentials(config)

        self.assertEqual(credentials.private_key, "test-private-key")
        self.assertEqual(credentials.account_address, "0xabc")
        self.assertNotIn("test-private-key", config.to_redacted_dict()["credentials"]["hyperliquid_trading_live_perp"].values())

    def test_sdk_loader_raises_typed_error_when_optional_sdk_missing(self) -> None:
        with patch.dict(sys.modules, {
            "eth_account": None,
            "hyperliquid": None,
            "hyperliquid.exchange": None,
            "hyperliquid.info": None,
        }):
            with self.assertRaises(HyperliquidSdkUnavailable):
                load_hyperliquid_sdk_binding(_config())

    def test_sdk_loader_builds_official_sdk_objects_when_modules_are_available(self) -> None:
        account_module = ModuleType("eth_account")
        account_module.Account = _FakeAccount
        hyperliquid_module = ModuleType("hyperliquid")
        exchange_module = ModuleType("hyperliquid.exchange")
        exchange_module.Exchange = _FakeExchange
        info_module = ModuleType("hyperliquid.info")
        info_module.Info = _FakeInfo

        with patch.dict(sys.modules, {
            "eth_account": account_module,
            "hyperliquid": hyperliquid_module,
            "hyperliquid.exchange": exchange_module,
            "hyperliquid.info": info_module,
        }):
            binding = load_hyperliquid_sdk_binding(_config())

        self.assertIsInstance(binding.exchange, _FakeExchange)
        self.assertIsInstance(binding.info, _FakeInfo)
        self.assertEqual(binding.exchange.wallet.private_key, "test-private-key")
        self.assertEqual(binding.exchange.account_address, "0xabc")
        self.assertEqual(binding.account_address, "0xabc")


def _config() -> KairosProjectConfig:
    return KairosProjectConfig(
        Path("/tmp/hyperliquid-sdk-loader"),
        Path("/tmp/hyperliquid-sdk-loader/kairos.toml"),
        {
            "providers": {
                "hyperliquid": {
                    "services": {
                        "execution_live": {
                            "credential": "hyperliquid_trading_live_perp",
                        },
                    },
                },
            },
            "credentials": {
                "hyperliquid_trading_live_perp": {
                    "private_key": "test-private-key",
                    "account_address": "0xabc",
                },
            },
        },
    )


class _FakeAccount:
    @staticmethod
    def from_key(private_key):
        return SimpleNamespace(private_key=private_key)


class _FakeExchange:
    def __init__(self, wallet, *, account_address):
        self.wallet = wallet
        self.account_address = account_address


class _FakeInfo:
    def __init__(self, *, skip_ws):
        self.skip_ws = skip_ws


if __name__ == "__main__":
    unittest.main()
