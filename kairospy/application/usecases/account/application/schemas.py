"""ExternalAccount broker and credential schemas used by account applications."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccountBrokerSchema:
    broker: str
    venue: str
    default_market: str
    credential_kind: str
    credential_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return self.broker

    def required_credential_fields(self) -> tuple[str, ...]:
        optional = set(self.optional_fields)
        return tuple(field for field in self.credential_fields if field not in optional)


AccountProviderSchema = AccountBrokerSchema

ACCOUNT_SCHEMAS: dict[str, AccountBrokerSchema] = {
    "paper": AccountBrokerSchema("paper", "paper", "spot", "none", (), ()),
    "binance": AccountBrokerSchema("binance", "binance", "spot", "api_key_secret", ("api_key", "api_secret")),
    "okx": AccountBrokerSchema("okx", "okx", "spot", "api_key_secret_passphrase", ("api_key", "api_secret", "passphrase")),
    "hyperliquid": AccountBrokerSchema(
        "hyperliquid", "hyperliquid", "swap", "wallet_private_key",
        ("wallet_address", "private_key", "vault_address"), ("vault_address",),
    ),
}

PROVIDER_ALIASES = {"okex": "okx", "ouyi": "okx"}


__all__ = ["ACCOUNT_SCHEMAS", "AccountBrokerSchema", "AccountProviderSchema", "PROVIDER_ALIASES"]
