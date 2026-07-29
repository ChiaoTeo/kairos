from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping, Sequence

from kairospy.application.system.facade.resources import DriverName, ExchangeName, broker
from kairospy.application.system.workspace import AccountRecord, KairosWorkspace
from kairospy.config import ConfigError


@dataclass(frozen=True, slots=True)
class AccountProviderSchema:
    provider: str
    venue: str
    default_market: str
    credential_kind: str
    credential_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()

    def required_credential_fields(self) -> tuple[str, ...]:
        optional = set(self.optional_fields)
        return tuple(field for field in self.credential_fields if field not in optional)


ACCOUNT_SCHEMAS: dict[str, AccountProviderSchema] = {
    "binance": AccountProviderSchema(
        provider="binance",
        venue="binance",
        default_market="spot",
        credential_kind="api_key_secret",
        credential_fields=("api_key", "api_secret"),
    ),
    "okx": AccountProviderSchema(
        provider="okx",
        venue="okx",
        default_market="spot",
        credential_kind="api_key_secret_passphrase",
        credential_fields=("api_key", "api_secret", "passphrase"),
    ),
    "hyperliquid": AccountProviderSchema(
        provider="hyperliquid",
        venue="hyperliquid",
        default_market="swap",
        credential_kind="wallet_private_key",
        credential_fields=("wallet_address", "private_key", "vault_address"),
        optional_fields=("vault_address",),
    ),
}

PROVIDER_ALIASES = {
    "okex": "okx",
    "ouyi": "okx",
}


class AccountFacade:
    def list_accounts(self) -> dict[str, object]:
        store = KairosWorkspace.resolve().accounts
        accounts = [account.to_dict() for account in store.list()]
        return {"accounts": accounts, "count": len(accounts), "root": str(store.root)}

    def schemas(self) -> dict[str, object]:
        return {"schemas": {name: _schema_payload(schema) for name, schema in ACCOUNT_SCHEMAS.items()}}

    def schema(self, provider: str) -> dict[str, object]:
        return _schema_payload(_provider_schema(provider))

    def create(
        self,
        *,
        account_id: str,
        provider: str,
        environment: str,
        venue: str | None,
        market: str | None,
        currency: str,
        credential_kind: str | None,
        credential: str | None,
        api_key: str | None,
        api_secret: str | None,
        passphrase: str | None,
        wallet_address: str | None,
        private_key: str | None,
        vault_address: str | None,
        field_values: Sequence[str] | None,
        credential_values: Sequence[str] | None,
        force: bool,
    ) -> str:
        workspace = KairosWorkspace.resolve()
        provider_schema = _provider_schema(provider)
        account_values = _pairs(field_values)
        credential_field_values = {
            **_provided_credential_values(
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                wallet_address=wallet_address,
                private_key=private_key,
                vault_address=vault_address,
            ),
            **_pairs(credential_values),
        }
        path = workspace.accounts_root / f"{account_id}.toml"
        if path.exists() and not force:
            raise ValueError(f"account already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _account_template(
                account_id,
                provider=provider_schema.provider,
                environment=environment,
                venue=venue or provider_schema.venue,
                market=market or provider_schema.default_market,
                currency=currency,
                credential=credential,
                credential_kind=credential_kind or provider_schema.credential_kind,
                credential_fields=provider_schema.credential_fields,
                credential_values=credential_field_values,
                account_values=account_values,
            ),
            encoding="utf-8",
        )
        workspace.operations.append(
            "account.create",
            target={"account": account_id},
            payload={
                "path": path,
                "provider": provider_schema.provider,
                "environment": environment,
                "venue": venue or provider_schema.venue,
                "market": market or provider_schema.default_market,
            },
        )
        return str(path)

    def delete(self, account_id: str, *, force: bool) -> str:
        workspace = KairosWorkspace.resolve()
        account = _account(account_id)
        path = account.source_path or workspace.accounts_root / f"{account_id}.toml"
        if path.parent != workspace.accounts_root:
            raise ValueError(f"refusing to delete account outside accounts root: {path}")
        journal = workspace.workspace_root / "accounts" / "journals" / f"{account.account_id}.jsonl"
        if journal.exists() and not force:
            raise ValueError(f"account journal exists; use --force to delete account config only: {journal}")
        path.unlink()
        workspace.operations.append("account.delete", target={"account": account.account_id}, payload={"path": path, "journal": journal})
        return str(path)

    def show(self, account_id: str, *, reveal_secrets: bool) -> dict[str, object]:
        return _account(account_id).to_dict(include_secret_values=reveal_secrets)

    def balance(self, account_id: str, *, params: Mapping[str, object] | None) -> dict[str, object]:
        account = _account(account_id)
        return {"account": account.account_id, "balance": self._broker(account).fetch_balance(params=params)}

    def open_orders(
        self,
        account_id: str,
        *,
        symbol: str | None,
        limit: int | None,
        params: Mapping[str, object] | None,
    ) -> dict[str, object]:
        account = _account(account_id)
        orders = tuple(self._broker(account).fetch_open_orders(symbol, limit=limit, params=params))
        return {"account": account.account_id, "orders": orders, "count": len(orders)}

    def snapshot(self, account_id: str, *, symbol: str | None, params: Mapping[str, object] | None) -> dict[str, object]:
        workspace = KairosWorkspace.resolve()
        account = _account(account_id)
        client = self._broker(account)
        payload = {
            "account": account.account_id,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "balance": client.fetch_balance(params=params),
            "open_orders": tuple(client.fetch_open_orders(symbol, params=params)),
        }
        path = workspace.workspace_root / "accounts" / "journals" / f"{account.account_id}.jsonl"
        _append_jsonl(path, payload)
        workspace.operations.append("account.snapshot", target={"account": account.account_id}, payload={"journal": path})
        return payload

    def doctor(self, account_id: str) -> dict[str, object]:
        account = _account(account_id)
        issues: list[str] = []
        if not account.provider:
            issues.append("provider is required")
        try:
            provider_schema = _provider_schema(account.provider)
        except ValueError as error:
            provider_schema = None
            issues.append(str(error))
        if account.environment == "live" and not account.credential_values and not account.credential:
            issues.append("live account has no credential metadata")
        if provider_schema is not None and account.credential_values:
            for key in provider_schema.required_credential_fields():
                value = account.credential_values.get(key)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"credential.{key} is empty")
            unknown = sorted(set(account.credential_values) - set(provider_schema.credential_fields) - {"kind", "ip_bound"})
            if unknown:
                issues.append(f"credential has unknown fields for {provider_schema.provider}: {', '.join(unknown)}")
        return {"account": account.to_dict(), "valid": not issues, "issues": issues}

    def _broker(self, account: AccountRecord):
        return broker(_exchange(account), DriverName.ccxt, credential=account.credential)


def _account(account_id: str) -> AccountRecord:
    try:
        return KairosWorkspace.resolve().accounts.get(account_id)
    except ConfigError as error:
        raise ValueError(str(error)) from error


def _exchange(account: AccountRecord) -> ExchangeName:
    value = (account.venue or account.provider).strip().lower()
    try:
        return ExchangeName(value)
    except ValueError as error:
        raise ValueError(f"unsupported account venue/provider: {value}") from error


def _provider_schema(provider: str) -> AccountProviderSchema:
    normalized = _normalize_provider(provider)
    try:
        return ACCOUNT_SCHEMAS[normalized]
    except KeyError as error:
        supported = ", ".join(sorted(ACCOUNT_SCHEMAS))
        raise ValueError(f"unsupported account provider: {provider}; supported: {supported}") from error


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower().replace("-", "_")
    return PROVIDER_ALIASES.get(normalized, normalized)


def _schema_payload(schema: AccountProviderSchema) -> dict[str, object]:
    return {
        "provider": schema.provider,
        "venue": schema.venue,
        "default_market": schema.default_market,
        "credential_kind": schema.credential_kind,
        "credential_fields": list(schema.credential_fields),
        "required_credential_fields": list(schema.required_credential_fields()),
        "optional_fields": list(schema.optional_fields),
    }


def _provided_credential_values(
    *,
    api_key: str | None,
    api_secret: str | None,
    passphrase: str | None,
    wallet_address: str | None,
    private_key: str | None,
    vault_address: str | None,
) -> dict[str, str]:
    values = {
        "api_key": api_key,
        "api_secret": api_secret,
        "passphrase": passphrase,
        "wallet_address": wallet_address,
        "private_key": private_key,
        "vault_address": vault_address,
    }
    return {key: value for key, value in values.items() if value is not None}


def _pairs(values: Sequence[str] | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in values or ():
        if "=" not in item:
            raise ValueError(f"field must be key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"field key is empty: {item}")
        pairs[key] = value
    return pairs


def _account_template(
    account_id: str,
    *,
    provider: str,
    environment: str,
    venue: str,
    market: str | None,
    currency: str,
    credential: str | None,
    credential_kind: str | None,
    credential_fields: Sequence[str],
    credential_values: Mapping[str, str],
    account_values: Mapping[str, str],
) -> str:
    lines = [
        "[account]",
        f'id = "{account_id}"',
        f'provider = "{provider}"',
        f'environment = "{environment}"',
        f'venue = "{venue}"',
    ]
    if market is not None:
        lines.append(f'market = "{market}"')
    lines.append(f'currency = "{currency}"')
    if credential is not None:
        lines.append(f'credential = "{_toml_escape(credential)}"')
    for key, value in sorted(account_values.items()):
        lines.append(f'{key} = "{_toml_escape(value)}"')
    if credential_kind is not None:
        lines.extend(["", "[credential]", f'kind = "{credential_kind}"', "ip_bound = true"])
        for field in credential_fields:
            lines.append(f'{field} = "{_toml_escape(credential_values.get(field, ""))}"')
        for key, value in sorted(credential_values.items()):
            if key not in credential_fields:
                lines.append(f'{key} = "{_toml_escape(value)}"')
    return "\n".join(lines) + "\n"


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["ACCOUNT_SCHEMAS", "AccountFacade", "AccountProviderSchema"]
