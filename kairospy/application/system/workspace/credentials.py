from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import os
import tomllib

from kairospy.config import ConfigError


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    credential_id: str
    provider: str
    kind: str | None = None
    source_path: Path | None = None
    values: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self, *, include_secret_values: bool = False) -> dict[str, object]:
        values = dict(self.values)
        if not include_secret_values:
            values = {key: _redact_secret(key, value) for key, value in values.items()}
        return {
            "credential_id": self.credential_id,
            "broker": self.provider,
            "provider": self.provider,
            "kind": self.kind,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "values": values,
        }


class CredentialStore:
    def __init__(self, credentials: Mapping[str, CredentialRecord], *, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._credentials = dict(sorted(credentials.items()))

    @classmethod
    def load(cls, root: str | Path) -> "CredentialStore":
        credentials: dict[str, CredentialRecord] = {}
        credential_root = Path(root).expanduser()
        if credential_root.exists():
            for path in sorted(credential_root.glob("*.toml")):
                record = _load_credential_file(path)
                credentials[record.credential_id] = record
        return cls(credentials, root=credential_root)

    def list(self) -> tuple[CredentialRecord, ...]:
        return tuple(self._credentials.values())

    def get(self, credential_id: str) -> CredentialRecord:
        try:
            return self._credentials[credential_id]
        except KeyError as error:
            raise ConfigError(f"unknown credential: {credential_id}") from error

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "credentials": [credential.to_dict() for credential in self.list()],
            "count": len(self._credentials),
        }


def _load_credential_file(path: Path) -> CredentialRecord:
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in credential config {path}: {error}") from error
    if not isinstance(values, Mapping):
        raise ConfigError(f"credential config root must be a TOML table: {path}")
    credential = values.get("credential")
    if not isinstance(credential, Mapping):
        raise ConfigError(f"[credential] table is required in credential config: {path}")
    credential_id = _optional_text(credential.get("id")) or path.stem
    provider = _optional_text(credential.get("broker")) or _required_text(credential.get("provider"), f"{path}: credential.broker or credential.provider")
    return CredentialRecord(
        credential_id=credential_id,
        provider=provider,
        kind=_optional_text(credential.get("kind")),
        source_path=path,
        values=dict(credential),
    )


def write_credential_file(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_credential_toml(payload), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _credential_toml(payload: Mapping[str, object]) -> str:
    lines = ["[credential]"]
    for key, value in payload.items():
        if value is None:
            continue
        lines.append(f'{key} = "{_toml_escape(str(value))}"')
    return "\n".join(lines) + "\n"


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


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = ["CredentialRecord", "CredentialStore", "write_credential_file"]
