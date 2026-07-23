from __future__ import annotations

from dataclasses import dataclass

from kairospy.infrastructure.configuration import KairosProjectConfig
from kairospy.integrations.config import resolve_hyperliquid_trading_credentials


class HyperliquidSdkUnavailable(RuntimeError):
    """Raised when the optional official Hyperliquid SDK cannot be imported."""


@dataclass(frozen=True, slots=True)
class HyperliquidSdkBinding:
    exchange: object
    info: object
    account_address: str


def load_hyperliquid_sdk_binding(config: KairosProjectConfig) -> HyperliquidSdkBinding:
    credentials = resolve_hyperliquid_trading_credentials(config)
    try:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
    except ImportError as error:
        raise HyperliquidSdkUnavailable(
            "Hyperliquid live execution requires the official hyperliquid-python-sdk and eth-account packages"
        ) from error
    wallet = Account.from_key(credentials.private_key)
    info = Info(skip_ws=True)
    exchange = Exchange(wallet, account_address=credentials.account_address)
    return HyperliquidSdkBinding(exchange, info, credentials.account_address)


__all__ = [
    "HyperliquidSdkBinding",
    "HyperliquidSdkUnavailable",
    "load_hyperliquid_sdk_binding",
]
