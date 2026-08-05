"""Account configuration records and the account configuration store.

This is the application-side representation of locally configured bindings.
It is deliberately separate from the Workspace domain, which owns paths and
workspace lifecycle but not account business configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping
import tomllib

from kairospy.domain.account import AccountModel, AccountSegment, ExternalAccountIdentity, ProductFamily, account_segment_from_name


_DEFAULT_ACCOUNT_SEGMENTS: dict[str, tuple[str, ...]] = {
    "binance": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures"),
    "okx": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures"),
    "okex": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures"),
    "hyperliquid": ("swap",),
}


class AccountConfigurationError(ValueError):
    """Invalid or inaccessible local account configuration."""


@dataclass(frozen=True, slots=True)
class AccountSegmentRecord:
    key: str
    model: AccountModel
    product_family: ProductFamily | None = None
    qualifier: str = ""
    alias: str | None = None
    values: Mapping[str, object] = field(default_factory=dict)

    def to_segment(self, identity: ExternalAccountIdentity) -> AccountSegment:
        return AccountSegment(identity, self.key, model=self.model, product_family=self.product_family, qualifier=self.qualifier)

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "model": self.model.value,
            "product_family": None if self.product_family is None else self.product_family.value,
            "qualifier": self.qualifier,
            "alias": self.alias,
            "values": dict(self.values),
        }


@dataclass(frozen=True, slots=True)
class AccountCredentialRecord:
    name: str
    ref: str | None = None
    kind: str | None = None
    role: str = "readonly"
    values: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self, *, include_secret_values: bool = False) -> dict[str, object]:
        values = dict(self.values)
        if not include_secret_values:
            values = {key: _redact_secret(key, value) for key, value in values.items()}
        return {"name": self.name, "ref": self.ref, "kind": self.kind, "role": self.role, "values": values}


@dataclass(frozen=True, slots=True)
class AccountRecord:
    account_id: str
    broker: str
    environment: str
    venue: str | None = None
    remote_identity: str | None = None
    default_segment: str | None = None
    credential: str | None = None
    source_path: Path | None = None
    credential_values: Mapping[str, object] = field(default_factory=dict)
    credentials: tuple[AccountCredentialRecord, ...] = ()
    permissions: Mapping[str, object] = field(default_factory=dict)
    initial_balances: tuple[tuple[str, Decimal], ...] = ()
    segments: tuple[AccountSegmentRecord, ...] = ()
    values: Mapping[str, object] = field(default_factory=dict)

    @property
    def provider(self) -> str:
        return self.broker

    @property
    def identity(self) -> ExternalAccountIdentity:
        return ExternalAccountIdentity(self.venue or self.broker, self.remote_identity or self.account_id)

    @property
    def account_key(self) -> str:
        return _account_key(self.identity)

    def to_dict(self, *, include_secret_values: bool = False) -> dict[str, object]:
        credential_values = dict(self.credential_values)
        if not include_secret_values:
            credential_values = {key: _redact_secret(key, value) for key, value in credential_values.items()}
        return {
            "account_id": self.account_id,
            "broker": self.broker,
            "provider": self.broker,
            "environment": self.environment,
            "venue": self.venue,
            "remote_identity": self.remote_identity,
            "default_segment": self.default_segment,
            "credential": self.credential,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "permissions": dict(self.permissions),
            "initial_balances": {asset: str(quantity) for asset, quantity in self.initial_balances},
            "credential_values": credential_values,
            "credentials": [credential.to_dict(include_secret_values=include_secret_values) for credential in self.credentials],
            "segments": [segment.to_dict() for segment in self.segments],
            "values": dict(self.values),
        }


class AccountStore:
    def __init__(self, accounts: Mapping[str, AccountRecord], *, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._accounts = dict(sorted(accounts.items()))

    @classmethod
    def load(cls, root: str | Path) -> "AccountStore":
        account_root = Path(root).expanduser()
        accounts: dict[str, AccountRecord] = {}
        if account_root.exists():
            for path in sorted(account_root.glob("*.toml")):
                record = _load_account_file(path)
                accounts[record.account_id] = record
        return cls(accounts, root=account_root)

    def list(self) -> tuple[AccountRecord, ...]:
        return tuple(self._accounts.values())

    def get(self, account_id: str) -> AccountRecord:
        try:
            return self._accounts[account_id]
        except KeyError as error:
            raise AccountConfigurationError(f"unknown account: {account_id}") from error

    def to_dict(self) -> dict[str, object]:
        return {"root": str(self.root), "accounts": [account.to_dict() for account in self.list()], "count": len(self._accounts)}


def _load_account_file(path: Path) -> AccountRecord:
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise AccountConfigurationError(f"invalid TOML in account config {path}: {error}") from error
    if not isinstance(values, Mapping):
        raise AccountConfigurationError(f"account config root must be a TOML table: {path}")
    account = values.get("account")
    if not isinstance(account, Mapping):
        raise AccountConfigurationError(f"[account] table is required in account config: {path}")
    account_id = _optional_text(account.get("id")) or path.stem
    broker = _optional_text(account.get("broker")) or _required_text(account.get("provider"), f"{path}: account.broker or account.provider")
    environment = _required_text(account.get("environment"), f"{path}: account.environment")
    discovery = values.get("discovery")
    return AccountRecord(
        account_id=account_id,
        broker=broker,
        environment=environment,
        venue=_optional_text(account.get("venue")) or broker,
        remote_identity=_optional_text(discovery.get("remote_identity")) if isinstance(discovery, Mapping) else None,
        default_segment=_optional_text(account.get("default_segment")),
        credential=_optional_text(account.get("credential")),
        source_path=path,
        credential_values=dict(values.get("credential")) if isinstance(values.get("credential"), Mapping) else {},
        credentials=_account_credentials(values),
        permissions=dict(values.get("permissions")) if isinstance(values.get("permissions"), Mapping) else {},
        initial_balances=_initial_balances(values.get("initial_balances"), path),
        segments=_account_segments(account, values),
        values=dict(account),
    )


def _account_segments(account: Mapping[str, object], values: Mapping[str, object]) -> tuple[AccountSegmentRecord, ...]:
    segments = values.get("segments")
    if isinstance(segments, Mapping):
        records: list[AccountSegmentRecord] = []
        for key, raw in sorted(segments.items()):
            if not isinstance(raw, Mapping):
                raise AccountConfigurationError(f"segments.{key} must be a table")
            model_name = _optional_text(raw.get("model"))
            product_name = _optional_text(raw.get("product_family"))
            if model_name is not None:
                model = AccountModel(model_name)
                product_family = None if product_name is None else ProductFamily(product_name)
            else:
                segment = account_segment_from_name(ExternalAccountIdentity("config", "config"), product_name or str(key))
                model, product_family = segment.model, segment.product_family
            records.append(AccountSegmentRecord(str(key), model, product_family, qualifier=_optional_text(raw.get("qualifier")) or "", alias=_optional_text(raw.get("alias")), values=dict(raw)))
        return tuple(records)
    broker = _optional_text(account.get("broker")) or _optional_text(account.get("provider")) or ""
    default_segment = _optional_text(account.get("default_segment"))
    if default_segment is not None:
        segment = account_segment_from_name(ExternalAccountIdentity(broker, str(account.get("id") or "config")), default_segment)
        return (AccountSegmentRecord(default_segment, segment.model, segment.product_family),)
    names = _DEFAULT_ACCOUNT_SEGMENTS.get(broker.strip().lower().replace("-", "_"), ("spot",))
    identity = ExternalAccountIdentity(broker, str(account.get("id") or "config"))
    return tuple(AccountSegmentRecord(name, (segment := account_segment_from_name(identity, name)).model, segment.product_family) for name in names)


def _account_credentials(values: Mapping[str, object]) -> tuple[AccountCredentialRecord, ...]:
    credentials = values.get("credentials")
    if not isinstance(credentials, Mapping):
        return ()
    records: list[AccountCredentialRecord] = []
    for name, raw in sorted(credentials.items()):
        if not isinstance(raw, Mapping):
            raise AccountConfigurationError(f"credentials.{name} must be a table")
        records.append(AccountCredentialRecord(str(name), ref=_optional_text(raw.get("ref")), kind=_optional_text(raw.get("kind")), role=_credential_role(raw.get("role"), default_name=str(name)), values=dict(raw)))
    return tuple(records)


def _initial_balances(value: object, path: Path) -> tuple[tuple[str, Decimal], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise AccountConfigurationError(f"[initial_balances] must be a table in account config: {path}")
    balances: list[tuple[str, Decimal]] = []
    for asset, raw_quantity in value.items():
        name = str(asset).strip().upper()
        if not name:
            raise AccountConfigurationError(f"initial balance asset cannot be empty: {path}")
        try:
            quantity = Decimal(str(raw_quantity))
        except Exception as error:
            raise AccountConfigurationError(f"initial_balances.{name} must be decimal-compatible: {path}") from error
        if quantity < 0:
            raise AccountConfigurationError(f"initial_balances.{name} cannot be negative: {path}")
        balances.append((name, quantity))
    if len({asset for asset, _ in balances}) != len(balances):
        raise AccountConfigurationError(f"initial_balances cannot contain duplicate assets: {path}")
    return tuple(sorted(balances))


def _credential_role(value: object, *, default_name: str) -> str:
    text = _optional_text(value)
    role = default_name.strip().lower().replace("-", "_") if text is None else text.lower().replace("-", "_")
    if role in {"readonly", "read_only"}:
        return "readonly"
    if role == "trade":
        return "trade"
    if text is None:
        return "readonly"
    raise AccountConfigurationError(f"credential role must be readonly or trade: {value}")


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_text(value: object, source: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise AccountConfigurationError(f"{source} must be a non-empty string")
    return text


def _redact_secret(key: str, value: object) -> object:
    name = key.lower()
    if any(part in name for part in ("secret", "key", "password", "token", "private", "passphrase")):
        return "<redacted>" if value not in (None, "") else value
    return value


def _account_key(identity: ExternalAccountIdentity) -> str:
    return ".".join(_key_part(part) for part in (identity.broker, identity.account_id) if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in "".join(character if character.isalnum() else "_" for character in text).split("_") if part)


__all__ = ["AccountConfigurationError", "AccountCredentialRecord", "AccountRecord", "AccountSegmentRecord", "AccountStore"]
