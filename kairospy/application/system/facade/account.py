from __future__ import annotations

from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from typing import Callable, Mapping, Sequence

from kairospy.application.account_books import default_account_books
from kairospy.application.system.diagnostics import record_exception
from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.application.system.facade.resources import (
    DriverName,
    account_balance_client,
    account_bootstrap_client,
    order_query_client,
)
from kairospy.application.system.workspace import AccountRecord, write_credential_file
from kairospy.config import ConfigError
from kairospy.core.account import AccountBookKind, AccountBookRef
from kairospy.application.domain.account.routing import account_book_route
from kairospy.application.pagination import PageRequest, paginate


@dataclass(frozen=True, slots=True)
class AccountBrokerSchema:
    provider: str
    venue: str
    default_market: str
    credential_kind: str
    credential_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()

    @property
    def broker(self) -> str:
        return self.provider

    def required_credential_fields(self) -> tuple[str, ...]:
        optional = set(self.optional_fields)
        return tuple(field for field in self.credential_fields if field not in optional)


AccountProviderSchema = AccountBrokerSchema


ACCOUNT_SCHEMAS: dict[str, AccountBrokerSchema] = {
    "binance": AccountBrokerSchema(
        provider="binance",
        venue="binance",
        default_market="spot",
        credential_kind="api_key_secret",
        credential_fields=("api_key", "api_secret"),
    ),
    "okx": AccountBrokerSchema(
        provider="okx",
        venue="okx",
        default_market="spot",
        credential_kind="api_key_secret_passphrase",
        credential_fields=("api_key", "api_secret", "passphrase"),
    ),
    "hyperliquid": AccountBrokerSchema(
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
        store = resolve_workspace().accounts
        locks = {lock.account_key: lock.to_dict() for lock in resolve_workspace().account_locks.list()}
        accounts = []
        for account in store.list():
            payload = account.to_dict()
            payload["lock"] = locks.get(account.account_key)
            accounts.append(payload)
        return {"accounts": accounts, "count": len(accounts), "root": str(store.root)}

    def schemas(self) -> dict[str, object]:
        return {"schemas": {name: _schema_payload(schema) for name, schema in ACCOUNT_SCHEMAS.items()}}

    def schema(self, broker_name: str) -> dict[str, object]:
        return _schema_payload(_broker_schema(broker_name))

    def create(
        self,
        *,
        account_id: str,
        broker: str,
        environment: str,
        venue: str | None,
        market: str | None,
        currency: str,
        cash: str | None,
        fee_rate: str,
        credential_kind: str | None,
        credential: str | None,
        credential_role: str | None,
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
        workspace = resolve_workspace()
        broker_schema = _broker_schema(broker)
        environment_value = environment.strip().lower()
        parsed_cash = None if cash is None else _non_negative_decimal(cash, "cash")
        parsed_fee_rate = _non_negative_decimal(fee_rate, "fee_rate")
        include_simulated_fields = environment_value != "live"
        include_fee_rate = include_simulated_fields or parsed_fee_rate != 0
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
        if credential is not None and credential.strip().startswith("env:"):
            raise ValueError("credential must be a credential id, not an env: reference")
        credential_id = _created_credential_id(account_id, credential, credential_field_values)
        credential_role_value = _credential_role(credential_role)
        credential_ref = credential_id or credential
        resolved_credential_kind = _credential_kind(
            environment=environment_value,
            explicit=credential_kind,
            default=broker_schema.credential_kind,
            credential_values=credential_field_values,
        )
        path = workspace.accounts_root / f"{account_id}.toml"
        if path.exists() and not force:
            raise ValueError(f"account already exists: {path}")
        credential_path = None if credential_id is None else workspace.credentials_root / f"{credential_id}.toml"
        if credential_path is not None and credential_path.exists() and not force:
            raise ValueError(f"credential already exists: {credential_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if credential_path is not None:
            write_credential_file(
                credential_path,
                {
                    "id": credential_id,
                    "broker": broker_schema.broker,
                    "kind": resolved_credential_kind,
                    **credential_field_values,
                },
            )
        path.write_text(
            _account_template(
                account_id,
                provider=broker_schema.broker,
                environment=environment,
                venue=venue or broker_schema.venue,
                market=market or broker_schema.default_market,
                default_market=broker_schema.default_market,
                currency=currency,
                cash=parsed_cash if parsed_cash is not None else (Decimal("100000") if include_simulated_fields else None),
                fee_rate=parsed_fee_rate if include_fee_rate else None,
                credential=None,
                named_credential_ref=credential_ref,
                named_credential_role=credential_role_value if credential_ref is not None else None,
                credential_kind=None,
                credential_fields=broker_schema.credential_fields,
                credential_values={},
                account_values=account_values,
            ),
            encoding="utf-8",
        )
        workspace.operations.append(
            "account.create",
            target={"account": account_id},
            payload={
                "path": path,
                "broker": broker_schema.broker,
                "provider": broker_schema.broker,
                "environment": environment,
                "venue": venue or broker_schema.venue,
                "market": market or broker_schema.default_market,
                "credential": credential_ref,
                "credential_role": credential_role_value if credential_ref is not None else None,
                "credential_path": credential_path,
            },
        )
        return str(path)

    def add_credential(
        self,
        account_id: str,
        *,
        name: str,
        ref: str,
        check: bool,
        force: bool,
    ) -> str:
        workspace = resolve_workspace()
        account = _account(account_id)
        if ref.strip().startswith("env:"):
            raise ValueError("credential ref is not an env: reference")
        credential_name = name.strip()
        if not credential_name:
            raise ValueError("credential name is required")
        normalized_name = _credential_role(credential_name)
        existing = {credential.name for credential in account.credentials}
        if credential_name in existing and not force:
            raise ValueError(f"credential already exists: {credential_name}")
        credential_role = normalized_name
        if check:
            self._check_credential(account, ref=ref, role=credential_role)
        path = account.source_path or workspace.accounts_root / f"{account.account_id}.toml"
        text = path.read_text(encoding="utf-8")
        if credential_name in existing:
            raise ValueError("replacing credentials is not implemented yet; delete or edit the account file")
        path.write_text(text.rstrip() + "\n\n" + _credential_template(credential_name, ref=ref) + "\n", encoding="utf-8")
        workspace.operations.append(
            "account.credential.add",
            target={"account": account.account_id, "credential": credential_name},
            payload={"path": path, "ref": ref, "role": credential_role},
        )
        return str(path)

    def modify(
        self,
        account_id: str,
        *,
        broker: str | None,
        environment: str | None,
        venue: str | None,
        currency: str | None,
        cash: str | None,
        fee_rate: str | None,
        credential: str | None,
        clear_credential: bool,
        field_values: Sequence[str] | None,
    ) -> str:
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
        if (currency is not None or cash is not None or fee_rate is not None) and not _is_simulated_environment(target_environment):
            raise ValueError("currency, cash, and fee_rate can only be modified for simulated accounts")
        if currency is not None:
            updates["currency"] = currency
        if cash is not None:
            updates["cash"] = str(_non_negative_decimal(cash, "cash"))
        if fee_rate is not None:
            updates["fee_rate"] = str(_non_negative_decimal(fee_rate, "fee_rate"))
        if credential is not None:
            updates["credential"] = credential
        updates.update(_pairs(field_values))
        removals = {"credential"} if clear_credential else set()
        if not updates and not removals:
            raise ValueError("account modify requires at least one field to update")
        path = account.source_path or workspace.accounts_root / f"{account.account_id}.toml"
        text = path.read_text(encoding="utf-8")
        path.write_text(_rewrite_toml_table(text, "account", updates=updates, removals=removals), encoding="utf-8")
        workspace.operations.append(
            "account.modify",
            target={"account": account.account_id},
            payload={"path": path, "updates": sorted(updates), "removals": sorted(removals)},
        )
        return str(path)

    def delete(self, account_id: str, *, force: bool) -> str:
        workspace = resolve_workspace()
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
        account = _account(account_id)
        payload = account.to_dict(include_secret_values=reveal_secrets)
        lock = resolve_workspace().account_locks.get(account.account_key)
        payload["lock"] = None if lock is None else lock.to_dict()
        return payload

    def locks(self) -> dict[str, object]:
        workspace = resolve_workspace()
        locks = [lock.to_dict() for lock in workspace.account_locks.list()]
        return {"locks": locks, "count": len(locks), "root": str(workspace.account_locks.root)}

    def lock(self, account_id: str) -> dict[str, object]:
        account = _account(account_id)
        lock = resolve_workspace().account_locks.get(account.account_key)
        return {"account": account.account_id, "account_key": account.account_key, "lock": None if lock is None else lock.to_dict()}

    def release_lock(self, account_id: str, *, stale_only: bool, force: bool) -> dict[str, object]:
        workspace = resolve_workspace()
        account = _account(account_id)
        released = workspace.account_locks.release(account.account_key, force=force, stale_only=stale_only)
        if released:
            workspace.operations.append("account.lock.release.manual", target={"account": account.account_id}, payload={"stale_only": stale_only, "force": force})
        return {"account": account.account_id, "account_key": account.account_key, "released": released}

    def balance(
        self,
        account_id: str,
        *,
        books: Sequence[str] | None,
        include_zero: bool,
        page: int,
        page_size: int,
        params: Mapping[str, object] | None,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        account = _account(account_id)
        selected_books = _balance_books(account, books)
        raw_by_book: dict[str, Mapping[str, object]] = {}
        errors: list[dict[str, object]] = []
        rows: list[dict[str, object]] = []
        if progress is not None:
            progress({"event": "start", "account": account.account_id, "books": list(selected_books), "total": len(selected_books)})
        for index, book in enumerate(selected_books, start=1):
            book_ref = _account_book_ref(account, book)
            route = account_book_route(book_ref, broker=account.broker, base_params=params)
            if progress is not None:
                progress({"event": "book_start", "book": book, "index": index, "total": len(selected_books), "params": dict(route.balance_params)})
            started_at = time.monotonic()
            try:
                client = account_balance_client(book_ref, DriverName.ccxt, credential=_read_credential_ref(account))
                raw = client.fetch_balance(params=route.balance_params)
            except Exception as error:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                diagnostic = record_exception(
                    error,
                    operation="account.balance.fetch_book",
                    command="account balance",
                    context={
                        "account": account.account_id,
                        "broker": account.broker,
                        "venue": account.venue,
                        "book": book,
                        "params": dict(route.balance_params),
                        "duration_ms": elapsed_ms,
                    },
                )
                errors.append(
                    {
                        "book": book,
                        "error": str(error),
                        "error_type": diagnostic["error_type"],
                        "params": dict(route.balance_params),
                        "duration_ms": elapsed_ms,
                        "diagnostic_id": diagnostic["diagnostic_id"],
                        "diagnostic_path": diagnostic["diagnostic_path"],
                    }
                )
                if progress is not None:
                    progress(
                        {
                            "event": "book_error",
                            "book": book,
                            "index": index,
                            "total": len(selected_books),
                            "error": str(error),
                            "error_type": diagnostic["error_type"],
                            "diagnostic_id": diagnostic["diagnostic_id"],
                            "duration_ms": elapsed_ms,
                        }
                    )
                continue
            raw_by_book[book] = raw
            book_rows = _balance_rows(account, book=book, balance=raw, include_zero=include_zero)
            rows.extend(book_rows)
            if progress is not None:
                progress({"event": "book_done", "book": book, "index": index, "total": len(selected_books), "rows": len(book_rows)})
        paged_rows, page_result = paginate(rows, PageRequest(page=page, page_size=page_size))
        return {
            "account": account.account_id,
            "broker": account.broker,
            "books": list(selected_books),
            "rows": list(paged_rows),
            "page": page_result.to_dict(),
            "raw": {book: dict(value) for book, value in raw_by_book.items()},
            "errors": errors,
            "balance": raw_by_book.get(selected_books[0]) if len(selected_books) == 1 else None,
        }

    def open_orders(
        self,
        account_id: str,
        *,
        symbol: str | None,
        limit: int | None,
        params: Mapping[str, object] | None,
    ) -> dict[str, object]:
        account = _account(account_id)
        client = order_query_client(_account_book_ref(account), DriverName.ccxt, credential=_read_credential_ref(account))
        orders = tuple(client.fetch_open_orders(symbol, limit=limit, params=params))
        return {"account": account.account_id, "orders": orders, "count": len(orders)}

    def snapshot(self, account_id: str, *, symbol: str | None, params: Mapping[str, object] | None) -> dict[str, object]:
        workspace = resolve_workspace()
        account = _account(account_id)
        client = account_bootstrap_client(_account_book_ref(account), DriverName.ccxt, credential=_read_credential_ref(account))
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
        return {"account": account.to_dict(), "valid": not issues, "issues": issues}

    def _credential_profile_client(self, account: AccountRecord, ref: str):
        return account_balance_client(_account_book_ref(account), DriverName.ccxt, credential=ref)

    def _check_credential(self, account: AccountRecord, *, ref: str, role: str) -> None:
        profile = _credential_profile(self._credential_profile_client(account, ref), ref=ref)
        _require_credential_role(ref, profile, role)
        new_identity = _credential_identity(profile)
        existing_identities = [
            identity
            for credential in account.credentials
            if credential.ref
            for identity in [_credential_identity(_credential_profile(self._credential_profile_client(account, credential.ref), ref=credential.ref))]
            if identity is not None
        ]
        if account.credential:
            identity = _credential_identity(_credential_profile(self._credential_profile_client(account, account.credential), ref=account.credential))
            if identity is not None:
                existing_identities.append(identity)
        if existing_identities:
            if new_identity is None:
                raise ValueError(f"credential {ref} account identity could not be verified")
            expected = existing_identities[0]
            if any(identity != expected for identity in existing_identities) or new_identity != expected:
                raise ValueError(f"credential {ref} belongs to a different account")


def _account(account_id: str) -> AccountRecord:
    try:
        return resolve_workspace().accounts.get(account_id)
    except ConfigError as error:
        raise ValueError(str(error)) from error


def _account_book_ref(account: AccountRecord, book: str | None = None) -> AccountBookRef:
    selected_book = book or account.market or _first_account_book(account) or AccountBookKind.SPOT.value
    return AccountBookRef(account.venue or account.broker, account.account_id, selected_book)


def _first_account_book(account: AccountRecord) -> str | None:
    if not account.books:
        return None
    value = account.books[0].kind or account.books[0].key
    return value or None


def _balance_books(account: AccountRecord, books: Sequence[str] | None) -> tuple[str, ...]:
    requested = tuple(book.strip().lower().replace("-", "_") for book in books or () if book.strip())
    if requested:
        return requested
    broker = account.broker.strip().lower()
    return default_account_books(broker, fallback=account.market or AccountBookKind.SPOT.value)


def _balance_rows(account: AccountRecord, *, book: str, balance: Mapping[str, object], include_zero: bool) -> list[dict[str, object]]:
    assets = sorted(_balance_assets(balance))
    rows: list[dict[str, object]] = []
    for asset in assets:
        free = _balance_amount(balance, asset, "free")
        used = _balance_amount(balance, asset, "used")
        total = _balance_amount(balance, asset, "total")
        if not include_zero and _all_zero(free, used, total):
            continue
        rows.append(
            {
                "account": account.account_id,
                "broker": account.broker,
                "book": book,
                "asset": asset,
                "free": str(free),
                "used": str(used),
                "total": str(total),
            }
        )
    return rows


def _balance_assets(balance: Mapping[str, object]) -> set[str]:
    assets: set[str] = set()
    for key in ("free", "used", "total"):
        values = balance.get(key)
        if isinstance(values, Mapping):
            assets.update(str(asset) for asset in values)
    for asset, values in balance.items():
        if asset in {"free", "used", "total", "info", "timestamp", "datetime"}:
            continue
        if isinstance(values, Mapping):
            assets.add(str(asset))
    return assets


def _balance_amount(balance: Mapping[str, object], asset: str, key: str) -> Decimal:
    values = balance.get(key)
    if isinstance(values, Mapping) and asset in values:
        return _decimal_or_zero(values.get(asset))
    asset_values = balance.get(asset)
    if isinstance(asset_values, Mapping) and key in asset_values:
        return _decimal_or_zero(asset_values.get(key))
    return Decimal("0")


def _all_zero(*values: Decimal) -> bool:
    return all(value == 0 for value in values)


def _decimal_or_zero(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _read_credential_ref(account: AccountRecord) -> str | None:
    for credential in account.credentials:
        if credential.role == "readonly" and credential.ref:
            return credential.ref
    for credential in account.credentials:
        if credential.ref:
            return credential.ref
    return account.credential


def _broker_schema(provider: str) -> AccountBrokerSchema:
    normalized = _normalize_broker(provider)
    try:
        return ACCOUNT_SCHEMAS[normalized]
    except KeyError as error:
        supported = ", ".join(sorted(ACCOUNT_SCHEMAS))
        raise ValueError(f"unsupported account broker: {provider}; supported: {supported}") from error


def _normalize_broker(provider: str) -> str:
    normalized = provider.strip().lower().replace("-", "_")
    return PROVIDER_ALIASES.get(normalized, normalized)


def _schema_payload(schema: AccountBrokerSchema) -> dict[str, object]:
    balance_books = default_account_books(schema.broker, fallback=schema.default_market)
    return {
        "broker": schema.broker,
        "provider": schema.broker,
        "venue": schema.venue,
        "balance_books": list(balance_books),
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


def _created_credential_id(account_id: str, credential: str | None, credential_values: Mapping[str, str]) -> str | None:
    if not credential_values:
        return None
    credential_id = (credential or account_id).strip()
    if not credential_id:
        raise ValueError("credential id is required when credential values are provided")
    if ":" in credential_id:
        raise ValueError("credential must be a credential id, not an env: reference")
    return credential_id


def _credential_role(role: str | None) -> str:
    value = (role or "readonly").strip().lower().replace("-", "_")
    if value not in {"readonly", "read_only", "trade"}:
        raise ValueError("credential role must be readonly or trade")
    return "readonly" if value in {"readonly", "read_only"} else "trade"


def _credential_kind(
    *,
    environment: str,
    explicit: str | None,
    default: str,
    credential_values: Mapping[str, str],
) -> str | None:
    if explicit is not None:
        return explicit
    if environment in {"live", "testnet"} or credential_values:
        return default
    return None


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
    default_market: str,
    currency: str,
    cash: Decimal | None,
    fee_rate: Decimal | None,
    credential: str | None,
    named_credential_ref: str | None,
    named_credential_role: str | None,
    credential_kind: str | None,
    credential_fields: Sequence[str],
    credential_values: Mapping[str, str],
    account_values: Mapping[str, str],
) -> str:
    lines = [
        "[account]",
        f'id = "{account_id}"',
        f'broker = "{provider}"',
        f'environment = "{environment}"',
    ]
    if venue != provider:
        lines.append(f'venue = "{venue}"')
    if market is not None and market != default_market:
        lines.append(f'market = "{market}"')
    if currency != "USD":
        lines.append(f'currency = "{currency}"')
    if cash is not None:
        lines.append(f'cash = "{cash}"')
    if fee_rate is not None:
        lines.append(f'fee_rate = "{fee_rate}"')
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
    if named_credential_ref is not None and named_credential_role is not None:
        lines.extend(["", _credential_template(named_credential_role, ref=named_credential_ref).rstrip()])
    return "\n".join(lines) + "\n"


def _credential_template(name: str, *, ref: str) -> str:
    return "\n".join([f"[credentials.{_toml_key(name)}]", f'ref = "{_toml_escape(ref)}"'])


def _is_simulated_environment(environment: str) -> bool:
    return environment in {"backtest", "paper", "simulation", "sandbox"}


def _credential_profile(client: object, *, ref: str) -> Mapping[str, object]:
    inspector = getattr(client, "inspect_credential", None)
    if callable(inspector):
        value = inspector()
        if not isinstance(value, Mapping):
            raise ValueError(f"credential {ref} inspection did not return an object")
        return value
    try:
        client.fetch_balance(params={})
    except Exception as error:
        raise ValueError(f"credential {ref} cannot read private account data") from error
    return {"read_private": True}


def _require_credential_role(ref: str, profile: Mapping[str, object], role: str) -> None:
    permissions = _profile_permissions(profile)
    if "read" not in permissions:
        raise ValueError(f"credential {ref} cannot read private account data")
    if role == "trade" and "trade" not in permissions:
        raise ValueError(f"credential {ref} is not a trade credential")


def _profile_permissions(profile: Mapping[str, object]) -> set[str]:
    values: set[str] = set()
    for key in ("capabilities", "permissions", "scopes"):
        raw = profile.get(key)
        if isinstance(raw, str):
            values.add(_permission_key(raw))
        elif isinstance(raw, SequenceABC) and not isinstance(raw, (str, bytes)):
            values.update(_permission_key(str(item)) for item in raw)
        elif isinstance(raw, Mapping):
            values.update(_permission_key(str(name)) for name, enabled in raw.items() if bool(enabled))
    for key, permission in (
        ("can_read_private", "read"),
        ("read_private", "read"),
        ("read", "read"),
        ("can_trade", "trade"),
        ("trade", "trade"),
        ("trade_orders", "trade"),
    ):
        if profile.get(key) is True:
            values.add(permission)
    return values


def _credential_identity(profile: Mapping[str, object]) -> str | None:
    for key in ("account_key", "account_id", "uid", "user_id", "master_account_id", "sub_account", "subaccount"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        if isinstance(value, int):
            return str(value)
    account = profile.get("account")
    if isinstance(account, Mapping):
        return _credential_identity(account)
    return None


def _permission_key(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "read": "read",
        "readonly": "read",
        "read_only": "read",
        "read_private": "read",
        "trade": "trade",
        "order": "trade",
        "orders": "trade",
        "trade_orders": "trade",
        "place_order": "trade",
        "place_orders": "trade",
    }
    return aliases.get(normalized, normalized)


def _rewrite_toml_table(
    text: str,
    table: str,
    *,
    updates: Mapping[str, object],
    removals: set[str],
) -> str:
    lines = text.splitlines()
    header = f"[{table}]"
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        raise ValueError(f"{header} table is required")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break

    seen: set[str] = set()
    rewritten = list(lines[: start + 1])
    for line in lines[start + 1 : end]:
        key = _toml_assignment_key(line)
        if key is None:
            rewritten.append(line)
            continue
        if key in removals:
            seen.add(key)
            continue
        if key in updates:
            rewritten.append(f'{key} = {_toml_value(updates[key])}')
            seen.add(key)
            continue
        rewritten.append(line)
    for key, value in updates.items():
        if key not in seen:
            rewritten.append(f'{key} = {_toml_value(value)}')
    rewritten.extend(lines[end:])
    return "\n".join(rewritten).rstrip() + "\n"


def _toml_assignment_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    if not key or any(character.isspace() for character in key):
        return None
    return key


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return f'"{_toml_escape(str(value))}"'


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_key(value: str) -> str:
    if value and all(character.isalnum() or character in "_-" for character in value):
        return value
    return f'"{_toml_escape(value)}"'


def _non_negative_decimal(value: object, source: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{source} must be decimal-compatible") from error
    if parsed < 0:
        raise ValueError(f"{source} cannot be negative")
    return parsed


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


__all__ = ["ACCOUNT_SCHEMAS", "AccountBrokerSchema", "AccountFacade", "AccountProviderSchema"]
