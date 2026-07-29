from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import tomllib

from kairospy.config import ConfigError


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
    permissions: Mapping[str, object] = field(default_factory=dict)
    values: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self, *, include_secret_values: bool = False) -> dict[str, object]:
        credential_values = dict(self.credential_values)
        if not include_secret_values:
            credential_values = {key: _redact_secret(key, value) for key, value in credential_values.items()}
        return {
            "account_id": self.account_id,
            "provider": self.provider,
            "environment": self.environment,
            "venue": self.venue,
            "market": self.market,
            "credential": self.credential,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "permissions": dict(self.permissions),
            "credential_values": credential_values,
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
    provider = _required_text(account.get("provider"), f"{path}: account.provider")
    environment = _required_text(account.get("environment"), f"{path}: account.environment")
    credential = values.get("credential")
    permissions = values.get("permissions")
    return AccountRecord(
        account_id=account_id,
        provider=provider,
        environment=environment,
        venue=_optional_text(account.get("venue")) or provider,
        market=_optional_text(account.get("market")),
        credential=_optional_text(account.get("credential")),
        source_path=path,
        credential_values=dict(credential) if isinstance(credential, Mapping) else {},
        permissions=dict(permissions) if isinstance(permissions, Mapping) else {},
        values=dict(account),
    )


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
