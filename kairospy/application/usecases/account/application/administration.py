"""Local account administration use cases."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from kairospy.application.usecases.account.application.configuration import AccountConfigurationError, AccountRecord, AccountStore
from kairospy.application.usecases.account.application.results import AccountConfigurationPathResult, AccountDetailResult, AccountDoctorResult, AccountListResult, AccountSchemaResult, AccountSchemasResult
from kairospy.application.usecases.account.application.runtime import default_account_segments
from kairospy.application.usecases.account.application.schemas import ACCOUNT_SCHEMAS, AccountBrokerSchema, PROVIDER_ALIASES
from kairospy.application.usecases.account.services.configuration import AccountConfigurationWriter
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace


class AccountAdministrationApplication:
    """Manage local account bindings without reading remote account state."""

    def __init__(self) -> None:
        self._configuration = AccountConfigurationWriter()

    def list_accounts(self) -> AccountListResult:
        workspace = resolve_workspace()
        store = AccountStore.load(workspace.accounts_root)
        locks = {lock.account_key: lock.to_dict() for lock in workspace.account_locks.list()}
        accounts = []
        for account in store.list():
            payload = account.to_dict()
            payload["lock"] = locks.get(account.account_key)
            accounts.append(payload)
        return AccountListResult(tuple(accounts), len(accounts), str(store.root))

    def schemas(self) -> AccountSchemasResult:
        return AccountSchemasResult({name: _schema_payload(schema) for name, schema in ACCOUNT_SCHEMAS.items()})

    def schema(self, broker_name: str) -> AccountSchemaResult:
        schema = _broker_schema(broker_name)
        return AccountSchemaResult(schema.broker, schema.venue, schema.credential_kind, tuple(schema.required_credential_fields()), tuple(schema.optional_fields), tuple(schema.credential_fields))

    def modify(
        self,
        account_id: str,
        *,
        broker: str | None,
        environment: str | None,
        venue: str | None,
        fee_rate: str | None,
        credential: str | None,
        clear_credential: bool,
        field_values: Sequence[str] | None,
    ) -> AccountConfigurationPathResult:
        workspace = resolve_workspace()
        account = _account(account_id)
        if credential is not None and credential.strip().startswith("env:"):
            raise ValueError("credential must be a credential id, not an env: reference")
        if credential is not None and clear_credential:
            raise ValueError("--credential and --clear-credential cannot be used together")
        updates: dict[str, object] = {}
        if broker is not None:
            updates["broker"] = _broker_schema(broker).broker
        if environment is not None:
            updates["environment"] = environment.strip().lower()
        if venue is not None:
            updates["venue"] = venue
        target_environment = str(updates.get("environment") or account.environment).strip().lower()
        if fee_rate is not None and target_environment not in {"backtest", "paper", "simulation", "sandbox"}:
            raise ValueError("fee_rate can only be modified for simulated accounts")
        if fee_rate is not None:
            updates["fee_rate"] = str(_non_negative_decimal(fee_rate, "fee_rate"))
        if credential is not None:
            updates["credential"] = credential
        updates.update(_pairs(field_values))
        removals = {"credential"} if clear_credential else set()
        if not updates and not removals:
            raise ValueError("account modify requires at least one field to update")
        path = account.source_path or workspace.accounts_root / f"{account.account_id}.toml"
        self._configuration.rewrite_table(path, "account", updates=updates, removals=removals)
        workspace.operations.append("account.modify", target={"account": account.account_id}, payload={"path": path, "updates": sorted(updates), "removals": sorted(removals)})
        return AccountConfigurationPathResult(path)

    def delete(self, account_id: str, *, force: bool) -> AccountConfigurationPathResult:
        workspace = resolve_workspace()
        account = _account(account_id)
        path = account.source_path or workspace.accounts_root / f"{account_id}.toml"
        if path.parent != workspace.accounts_root:
            raise ValueError(f"refusing to delete account outside accounts root: {path}")
        journal = workspace.workspace_root / "accounts" / "journals" / f"{account.account_id}.jsonl"
        if journal.exists() and not force:
            raise ValueError(f"account journal exists; use --force to delete account config only: {journal}")
        self._configuration.delete_account(path)
        workspace.operations.append("account.delete", target={"account": account.account_id}, payload={"path": path, "journal": journal})
        return AccountConfigurationPathResult(path)

    def show(self, account_id: str, *, reveal_secrets: bool) -> AccountDetailResult:
        account = _account(account_id)
        payload = account.to_dict(include_secret_values=reveal_secrets)
        lock = resolve_workspace().account_locks.get(account.account_key)
        lock_payload = None if lock is None else lock.to_dict()
        payload["lock"] = lock_payload
        return AccountDetailResult(payload, lock_payload)

    def doctor(self, account_id: str) -> AccountDoctorResult:
        account = _account(account_id)
        issues: list[str] = []
        if not account.broker:
            issues.append("broker is required")
        try:
            broker_schema = _broker_schema(account.broker)
        except ValueError as error:
            broker_schema = None
            issues.append(str(error))
        if account.environment == "live" and not account.credential_values and not account.credential and not account.credentials:
            issues.append("live account has no credential metadata")
        if broker_schema is not None and account.credential_values:
            for key in broker_schema.required_credential_fields():
                value = account.credential_values.get(key)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"credential.{key} is empty")
            unknown = sorted(set(account.credential_values) - set(broker_schema.credential_fields) - {"kind", "ip_bound"})
            if unknown:
                issues.append(f"credential has unknown fields for {broker_schema.broker}: {', '.join(unknown)}")
        return AccountDoctorResult(account.to_dict(), not issues, tuple(issues))


def _account(account_id: str) -> AccountRecord:
    try:
        return AccountStore.load(resolve_workspace().accounts_root).get(account_id)
    except AccountConfigurationError as error:
        raise ValueError(str(error)) from error


def _broker_schema(broker: str) -> AccountBrokerSchema:
    normalized = _normalize_broker(broker)
    try:
        return ACCOUNT_SCHEMAS[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported account broker: {broker}; supported: {', '.join(sorted(ACCOUNT_SCHEMAS))}") from error


def _normalize_broker(broker: str) -> str:
    normalized = broker.strip().lower().replace("-", "_")
    return PROVIDER_ALIASES.get(normalized, normalized)


def _schema_payload(schema: AccountBrokerSchema) -> dict[str, object]:
    return {
        "broker": schema.broker,
        "provider": schema.broker,
        "venue": schema.venue,
        "balance_segments": list(default_account_segments(schema.broker, fallback=schema.default_market)),
        "default_market": schema.default_market,
        "credential_kind": schema.credential_kind,
        "credential_fields": list(schema.credential_fields),
        "required_credential_fields": list(schema.required_credential_fields()),
        "optional_fields": list(schema.optional_fields),
    }


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


def _non_negative_decimal(value: object, source: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{source} must be decimal-compatible") from error
    if parsed < 0:
        raise ValueError(f"{source} cannot be negative")
    return parsed


__all__ = ["AccountAdministrationApplication"]
