from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from kairospy.infrastructure.configuration import ConfigError, CredentialRef, KairosProjectConfig


@dataclass(frozen=True, slots=True)
class BinanceTradingCredentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True, slots=True)
class HyperliquidTradingCredentials:
    private_key: str
    account_address: str


@dataclass(frozen=True, slots=True)
class IbkrConnectionSettings:
    host: str
    port: int
    client_id: int


@dataclass(frozen=True, slots=True)
class CcxtExchangeSettings:
    exchange_id: str
    api_key: str | None = None
    api_secret: str | None = None
    password: str | None = None
    uid: str | None = None
    sandbox: bool = False
    enable_rate_limit: bool = True
    timeout_ms: int | None = None
    options: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderServiceConfig:
    provider: str
    service: str
    credential: str
    environment: str | None = None
    public: bool = False
    values: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AccountBinding:
    name: str
    account_ref: str
    provider: str
    environment: str
    credential: str
    permissions: tuple[str, ...]
    allowed_products: tuple[str, ...]
    capital_scope: str


class CredentialResolver:
    def __init__(self, config: KairosProjectConfig) -> None:
        self.config = config

    def field(self, credential: str, field: str, default: Any = None) -> CredentialRef:
        path = f"credentials.{credential}.{field}"
        return CredentialRef(credential, field, self.config.resolve(path, default))

    def required_string(self, credential: str, field: str, message: str) -> str:
        value = self.field(credential, field)
        if value.resolved in (None, ""):
            raise ConfigError(message)
        return str(value.resolved)


def resolve_massive_marketdata_config(config: KairosProjectConfig):
    from kairospy.integrations.connectors.massive.config import MassiveConfig

    resolver = CredentialResolver(config)
    api_key = resolver.required_string(
        "massive_marketdata_primary",
        "api_key",
        "Massive market data credential is missing. Set KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY "
        "or configure credentials.massive_marketdata_primary.api_key.",
    )
    timeout = int(config.resolve("providers.massive.services.historical_market_data.timeout_seconds", 30).resolved)
    retries = int(config.resolve("providers.massive.services.historical_market_data.max_retries", 4).resolved)
    return MassiveConfig(api_key, timeout_seconds=timeout, max_retries=retries)


def resolve_provider_service_config(
    config: KairosProjectConfig,
    provider: str,
    service: str,
    *,
    default_credential: str = "",
) -> ProviderServiceConfig:
    path = f"providers.{provider}.services.{service}"
    raw = config.get(path, {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"[{path}] must be a table")
    values = dict(raw)
    credential = str(values.get("credential") or default_credential)
    return ProviderServiceConfig(
        provider,
        service,
        credential,
        str(values["environment"]) if values.get("environment") is not None else None,
        bool(values.get("public", False)),
        values,
    )


def resolve_account_binding(config: KairosProjectConfig, name: str) -> AccountBinding:
    raw = config.get(f"accounts.{name}")
    if not isinstance(raw, dict):
        raise ConfigError(f"account binding does not exist: {name}")
    credential = str(raw.get("credential") or "")
    if not credential:
        raise ConfigError(f"accounts.{name}.credential is required")
    return AccountBinding(
        name,
        str(raw.get("account_ref") or ""),
        str(raw.get("provider") or ""),
        str(raw.get("environment") or ""),
        credential,
        tuple(str(item) for item in raw.get("permissions", ())),
        tuple(str(item) for item in raw.get("allowed_products", ())),
        str(raw.get("capital_scope") or ""),
    )


def resolve_binance_trading_credentials(
    config: KairosProjectConfig,
    environment: str = "testnet",
) -> BinanceTradingCredentials:
    if environment not in {"testnet", "live"}:
        raise ConfigError(f"unsupported Binance environment: {environment}")
    service = "execution_testnet" if environment == "testnet" else "execution_live"
    default_credential = (
        "binance_trading_testnet_spot"
        if environment == "testnet"
        else "binance_trading_live_spot"
    )
    credential = resolve_provider_service_config(
        config,
        "binance",
        service,
        default_credential=default_credential,
    ).credential
    prefix = (
        "KAIROS_BINANCE_TRADING_TESTNET_SPOT"
        if environment == "testnet"
        else "KAIROS_BINANCE_TRADING_LIVE_SPOT"
    )
    resolver = CredentialResolver(config)
    key = resolver.required_string(
        credential,
        "api_key",
        f"Binance {environment} trading credential is missing. Set {prefix}_API_KEY/{prefix}_API_SECRET "
        f"or configure credentials.{credential}.api_key and credentials.{credential}.api_secret.",
    )
    secret = resolver.required_string(
        credential,
        "api_secret",
        f"Binance {environment} trading credential is missing. Set {prefix}_API_KEY/{prefix}_API_SECRET "
        f"or configure credentials.{credential}.api_key and credentials.{credential}.api_secret.",
    )
    return BinanceTradingCredentials(key, secret)


def resolve_hyperliquid_trading_credentials(config: KairosProjectConfig) -> HyperliquidTradingCredentials:
    credential = resolve_provider_service_config(
        config,
        "hyperliquid",
        "execution_live",
        default_credential="hyperliquid_trading_live_perp",
    ).credential
    resolver = CredentialResolver(config)
    private_key = resolver.required_string(
        credential,
        "private_key",
        "Hyperliquid live trading credential is missing. Set KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY/"
        "KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS or configure credentials."
        f"{credential}.private_key and credentials.{credential}.account_address.",
    )
    account_address = resolver.required_string(
        credential,
        "account_address",
        "Hyperliquid live trading credential is missing. Set KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY/"
        "KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS or configure credentials."
        f"{credential}.private_key and credentials.{credential}.account_address.",
    )
    return HyperliquidTradingCredentials(private_key, account_address)


def resolve_ccxt_exchange_settings(
    config: KairosProjectConfig,
    provider: str,
    service: str = "execution_live",
    *,
    default_credential: str = "",
) -> CcxtExchangeSettings:
    service_config = resolve_provider_service_config(
        config,
        provider,
        service,
        default_credential=default_credential,
    )
    values = service_config.values or {}
    exchange_id = str(values.get("exchange_id") or provider).strip().lower()
    if not exchange_id:
        raise ConfigError(f"providers.{provider}.services.{service}.exchange_id is required")
    resolver = CredentialResolver(config)
    credential = service_config.credential
    api_key = _optional_credential_string(resolver, credential, "api_key") if credential else None
    api_secret = _optional_credential_string(resolver, credential, "api_secret") if credential else None
    password = _optional_credential_string(resolver, credential, "password") if credential else None
    uid = _optional_credential_string(resolver, credential, "uid") if credential else None
    raw_options = values.get("options", {})
    if raw_options is None:
        raw_options = {}
    if not isinstance(raw_options, dict):
        raise ConfigError(f"providers.{provider}.services.{service}.options must be a table")
    return CcxtExchangeSettings(
        exchange_id,
        api_key,
        api_secret,
        password,
        uid,
        sandbox=bool(values.get("sandbox", service_config.environment == "testnet")),
        enable_rate_limit=bool(values.get("enable_rate_limit", True)),
        timeout_ms=int(values["timeout_ms"]) if values.get("timeout_ms") is not None else None,
        options=dict(raw_options),
    )


def _optional_credential_string(resolver: CredentialResolver, credential: str, field: str) -> str | None:
    value = resolver.field(credential, field).resolved
    return None if value in (None, "") else str(value)


def resolve_ibkr_trading_connection(
    config: KairosProjectConfig | None,
    credential: str = "ibkr_trading_paper_main",
) -> IbkrConnectionSettings:
    if config is None:
        host = os.getenv("KAIROS_IBKR_TRADING_PAPER_MAIN_HOST")
        port = os.getenv("KAIROS_IBKR_TRADING_PAPER_MAIN_PORT")
        client_id = os.getenv("KAIROS_IBKR_TRADING_PAPER_MAIN_CLIENT_ID")
    else:
        resolver = CredentialResolver(config)
        host = resolver.field(credential, "host", "env:KAIROS_IBKR_TRADING_PAPER_MAIN_HOST").resolved
        port = resolver.field(credential, "port", "env:KAIROS_IBKR_TRADING_PAPER_MAIN_PORT").resolved
        client_id = resolver.field(credential, "client_id", "env:KAIROS_IBKR_TRADING_PAPER_MAIN_CLIENT_ID").resolved
    return IbkrConnectionSettings(str(host or "127.0.0.1"), int(port or "4001"), int(client_id or "51"))
