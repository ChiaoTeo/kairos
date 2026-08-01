from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import tomllib

from kairospy.application.account_books import default_account_books
from kairospy.config import ConfigError
from kairospy.core.account import AccountAlias, AccountBookRef, AccountDirectory, AccountIdentity


@dataclass(frozen=True, slots=True)
class AccountBookRecord:
    key: str
    kind: str
    qualifier: str = ""
    alias: str | None = None
    values: Mapping[str, object] = field(default_factory=dict)

    def to_ref(self, identity: AccountIdentity) -> AccountBookRef:
        return AccountBookRef(identity, book=self.kind, qualifier=self.qualifier)

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "kind": self.kind,
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
        return {
            "name": self.name,
            "ref": self.ref,
            "kind": self.kind,
            "role": self.role,
            "values": values,
        }


@dataclass(frozen=True, slots=True)
class AccountRecord:
    account_id: str
    provider: str
    environment: str
    venue: str | None = None
    market: str | None = None
    credential: str | None = None
    source_path: Path | None = None
    credential_values: Mapping[str, object] = field(default_factory=dict)
    credentials: tuple[AccountCredentialRecord, ...] = ()
    permissions: Mapping[str, object] = field(default_factory=dict)
    books: tuple[AccountBookRecord, ...] = ()
    values: Mapping[str, object] = field(default_factory=dict)

    @property
    def broker(self) -> str:
        return self.provider

    @property
    def identity(self) -> AccountIdentity:
        return AccountIdentity(self.venue or self.broker, self.account_id)

    @property
    def account_key(self) -> str:
        return _account_key(self.identity)

    @property
    def directory(self) -> AccountDirectory:
        aliases: list[AccountAlias] = []
        for book in self.books:
            ref = book.to_ref(self.identity)
            aliases.append(AccountAlias(book.alias or f"{self.account_key}.{_book_key(ref)}", ref))
        return AccountDirectory(tuple(aliases))

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
            "market": self.market,
            "credential": self.credential,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "permissions": dict(self.permissions),
            "credential_values": credential_values,
            "credentials": [credential.to_dict(include_secret_values=include_secret_values) for credential in self.credentials],
            "books": [book.to_dict() for book in self.books],
            "values": dict(self.values),
        }


class AccountStore:
    def __init__(self, accounts: Mapping[str, AccountRecord], *, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._accounts = dict(sorted(accounts.items()))

    @classmethod
    def load(cls, root: str | Path) -> "AccountStore":
        accounts: dict[str, AccountRecord] = {}
        account_root = Path(root).expanduser()
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
            raise ConfigError(f"unknown account: {account_id}") from error

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "accounts": [account.to_dict() for account in self.list()],
            "count": len(self._accounts),
        }

    def directory(self) -> AccountDirectory:
        aliases: list[AccountAlias] = []
        for account in self.list():
            aliases.extend(account.directory.aliases)
        return AccountDirectory(tuple(aliases))


def _load_account_file(path: Path) -> AccountRecord:
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in account config {path}: {error}") from error
    if not isinstance(values, Mapping):
        raise ConfigError(f"account config root must be a TOML table: {path}")
    account = values.get("account")
    if not isinstance(account, Mapping):
        raise ConfigError(f"[account] table is required in account config: {path}")
    account_id = _optional_text(account.get("id")) or path.stem
    provider = _optional_text(account.get("broker")) or _required_text(account.get("provider"), f"{path}: account.broker or account.provider")
    environment = _required_text(account.get("environment"), f"{path}: account.environment")
    credential = values.get("credential")
    permissions = values.get("permissions")
    books = _account_books(account, values)
    credential_records = _account_credentials(values)
    return AccountRecord(
        account_id=account_id,
        provider=provider,
        environment=environment,
        venue=_optional_text(account.get("venue")) or provider,
        market=_optional_text(account.get("market")),
        credential=_optional_text(account.get("credential")),
        source_path=path,
        credential_values=dict(credential) if isinstance(credential, Mapping) else {},
        credentials=credential_records,
        permissions=dict(permissions) if isinstance(permissions, Mapping) else {},
        books=books,
        values=dict(account),
    )


def _account_books(account: Mapping[str, object], values: Mapping[str, object]) -> tuple[AccountBookRecord, ...]:
    books = values.get("books")
    if isinstance(books, Mapping):
        records = []
        for key, raw in sorted(books.items()):
            if not isinstance(raw, Mapping):
                raise ConfigError(f"books.{key} must be a table")
            kind = _optional_text(raw.get("kind")) or str(key)
            records.append(
                AccountBookRecord(
                    str(key),
                    kind,
                    qualifier=_optional_text(raw.get("qualifier")) or "",
                    alias=_optional_text(raw.get("alias")),
                    values=dict(raw),
                )
            )
        return tuple(records)
    broker = _optional_text(account.get("broker")) or _optional_text(account.get("provider")) or ""
    market = _optional_text(account.get("market"))
    if market is not None:
        return (AccountBookRecord(market, market),)
    return tuple(AccountBookRecord(book, book) for book in default_account_books(broker))


def _account_credentials(values: Mapping[str, object]) -> tuple[AccountCredentialRecord, ...]:
    credentials = values.get("credentials")
    if not isinstance(credentials, Mapping):
        return ()
    records = []
    for name, raw in sorted(credentials.items()):
        if not isinstance(raw, Mapping):
            raise ConfigError(f"credentials.{name} must be a table")
        records.append(
            AccountCredentialRecord(
                str(name),
                ref=_optional_text(raw.get("ref")),
                kind=_optional_text(raw.get("kind")),
                role=_credential_role(raw.get("role"), default_name=str(name)),
                values=dict(raw),
            )
        )
    return tuple(records)


def _credential_role(value: object, *, default_name: str) -> str:
    text = _optional_text(value)
    role = default_name.strip().lower().replace("-", "_") if text is None else text.lower().replace("-", "_")
    if role in {"readonly", "read_only"}:
        return "readonly"
    if role == "trade":
        return "trade"
    if text is None:
        return "readonly"
    raise ConfigError(f"credential role must be readonly or trade: {value}")


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_text(value: object, source: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ConfigError(f"{source} must be a non-empty string")
    return text


def _redact_secret(key: str, value: object) -> object:
    name = key.lower()
    if any(part in name for part in ("secret", "key", "password", "token", "private", "passphrase")):
        return "<redacted>" if value not in (None, "") else value
    return value


def _account_key(identity: AccountIdentity) -> str:
    return ".".join(_key_part(part) for part in (identity.broker, identity.account_id) if part)


def _book_key(book: AccountBookRef) -> str:
    return ".".join(_key_part(part) for part in book.book_key.split(":") if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)
