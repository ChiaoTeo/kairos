"""Workspace-owned credential configuration and private value storage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import TypedDict

from kairospy.system.domain.workspace import Workspace
from kairospy.system.apps.configuration.services.transactions import (
    WorkspaceConfigurationTransaction,
)


_PROVIDER_FIELDS: Mapping[str, tuple[str, ...]] = {
    "paper": (),
    "simulated": (),
    "ibkr": (),
    "massive": ("api_key",),
    "binance": ("api_key", "api_secret"),
    "okx": ("api_key", "api_secret", "passphrase"),
    "openai": ("api_key",),
    "anthropic": ("api_key",),
    "openrouter": ("api_key",),
    "custom-model": ("api_key",),
    "ollama": (),
    "lmstudio": (),
    "feishu": ("webhook_url",),
    "telegram": ("bot_token",),
}


class CredentialSchema(TypedDict):
    """Required credential fields for a supported provider."""

    provider: str
    required_fields: list[str]


@dataclass(frozen=True, slots=True)
class PreparedCredential:
    """Validated credential document ready for an atomic Workspace commit."""

    workspace: Workspace
    credential_id: str
    provider: str
    role: str
    values: Mapping[str, str] = field(repr=False)
    document: str = field(init=False, repr=False)

    def __init__(
        self,
        workspace: Workspace,
        credential_id: str,
        provider: str,
        role: str,
        values: Mapping[str, str],
    ) -> None:
        normalized = dict(values)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "credential_id", credential_id)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "values", normalized)
        object.__setattr__(
            self,
            "document",
            _credential_document(credential_id, provider, role, normalized),
        )

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        root = self.workspace.paths.credentials_root()
        _ensure_private_directory(root)
        transaction.stage_text(root / f"{self.credential_id}.toml", self.document)

    def discard(self) -> None:
        """Prepared values are memory-only, so discarding has no filesystem work."""


@dataclass(frozen=True, slots=True)
class CredentialConfigurationApplication:
    """Configure and resolve complete Workspace-local credentials."""

    workspace: Workspace

    @property
    def root(self) -> Path:
        return self.workspace.paths.credentials_root()

    def schema(self, provider: str) -> CredentialSchema:
        provider = provider.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
            raise ValueError(f"invalid credential provider: {provider}")
        fields = _PROVIDER_FIELDS.get(provider, ())
        return {"provider": provider, "required_fields": list(fields)}

    def configure(
        self,
        credential_id: str,
        *,
        provider: str,
        values: Mapping[str, str],
        role: str = "readonly",
        overwrite: bool = False,
    ) -> dict[str, object]:
        path = self.root / f"{_safe_id(credential_id, 'credential_id')}.toml"
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        prepared = self.prepare(
            credential_id,
            provider=provider,
            values=values,
            role=role,
        )
        _ensure_private_directory(self.root)
        _write_private_atomic(path, prepared.document)
        return self.show(prepared.credential_id)

    def prepare(
        self,
        credential_id: str,
        *,
        provider: str,
        values: Mapping[str, str],
        role: str = "readonly",
    ) -> PreparedCredential:
        credential_id = _safe_id(credential_id, "credential_id")
        provider = provider.strip().lower()
        schema = self.schema(provider)
        normalized = {
            _field_name(name): stripped
            for name, value in values.items()
            if (stripped := value.strip())
        }
        missing = [
            name
            for name in schema["required_fields"]
            if not normalized.get(str(name), "")
        ]
        if missing:
            raise ValueError(
                f"credential {credential_id} is missing required values: "
                + ", ".join(str(name) for name in missing)
            )
        return PreparedCredential(
            self.workspace,
            credential_id,
            provider,
            role.strip() or "readonly",
            normalized,
        )

    def list(self) -> list[dict[str, object]]:
        if not self.root.is_dir():
            return []
        result: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*.toml")):
            try:
                result.append(self._summary(path))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
                result.append(
                    {
                        "credential_id": path.stem,
                        "provider": "unknown",
                        "configured": False,
                        "issues": ["credential configuration is invalid"],
                    }
                )
        return result

    def show(self, credential_id: str) -> dict[str, object]:
        credential_id = _safe_id(credential_id, "credential_id")
        path = self.root / f"{credential_id}.toml"
        if not path.is_file():
            raise KeyError(f"credential does not exist: {credential_id}")
        return self._summary(path)

    def delete(self, credential_id: str) -> dict[str, str]:
        credential_id = _safe_id(credential_id, "credential_id")
        path = self.root / f"{credential_id}.toml"
        if not path.is_file():
            raise KeyError(f"credential does not exist: {credential_id}")
        path.unlink()
        directory_descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return {"credential_id": credential_id, "status": "deleted"}

    def resource_snapshot(self, credential_id: str) -> dict[str, object]:
        """Return stable credential identity without secret values."""

        summary = self.show(credential_id)
        credential = _credential_table(self.root / f"{credential_id}.toml")
        values = credential.get("values")
        value_hash = hashlib.sha256(
            json.dumps(
                dict(values) if isinstance(values, Mapping) else {},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        identity = {
            "credential_id": credential_id,
            "provider": summary.get("provider"),
            "role": summary.get("role"),
            "fields": summary.get("fields", []),
            "value_hash": value_hash,
        }
        return {
            **{key: value for key, value in identity.items() if key != "value_hash"},
            "configured": summary.get("configured", False),
            "resource_hash": hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
        }

    def resolve_field(self, credential_id: str, field: str) -> str | None:
        credential_id = _safe_id(credential_id, "credential_id")
        field = _field_name(field)
        value = _credential_table(self.root / f"{credential_id}.toml")
        values = value.get("values")
        if not isinstance(values, Mapping):
            return None
        raw = values.get(field)
        return raw.strip() or None if isinstance(raw, str) else None

    def _summary(self, path: Path) -> dict[str, object]:
        value = _credential_table(path)
        credential_id = _safe_id(str(value.get("id", path.stem)), "credential_id")
        provider = str(value.get("provider", "")).lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
            raise ValueError(f"invalid credential provider: {provider}")
        raw_values = value.get("values")
        values = raw_values if isinstance(raw_values, Mapping) else {}
        fields = sorted(
            str(name)
            for name, raw in values.items()
            if isinstance(name, str) and isinstance(raw, str) and raw.strip()
        )
        missing = [
            name for name in _PROVIDER_FIELDS.get(provider, ()) if name not in fields
        ]
        issues = [f"missing required values: {', '.join(missing)}"] if missing else []
        return {
            "credential_id": credential_id,
            "provider": provider,
            "role": str(value.get("role", "readonly")),
            "configured": not issues,
            "fields": fields,
            "field_available": {name: name in fields for name in fields},
            "issues": issues,
        }


def _credential_table(path: Path) -> Mapping[str, object]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        raise ValueError(f"invalid credential TOML: {path}") from None
    credential = value.get("credential")
    if not isinstance(credential, Mapping):
        raise ValueError(f"credential TOML requires [credential]: {path}")
    credential_id = str(credential.get("id", path.stem))
    if credential_id != path.stem:
        raise ValueError(f"credential id must match its file name: {path}")
    return credential


def _field_name(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise ValueError(f"invalid credential field: {value}")
    return value


def _safe_id(value: str, name: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError(f"{name} must be a path-safe identifier")
    return value


def _toml_string(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        escaped.append(
            {
                '"': '\\"',
                "\\": "\\\\",
                "\b": "\\b",
                "\t": "\\t",
                "\n": "\\n",
                "\f": "\\f",
                "\r": "\\r",
            }.get(
                character,
                f"\\u{ord(character):04X}" if ord(character) < 0x20 else character,
            )
        )
    return '"' + "".join(escaped) + '"'


def _credential_document(
    credential_id: str,
    provider: str,
    role: str,
    values: Mapping[str, str],
) -> str:
    document = [
        "[credential]",
        f"id = {_toml_string(credential_id)}",
        f"provider = {_toml_string(provider)}",
        f"role = {_toml_string(role)}",
    ]
    if values:
        document.extend(("", "[credential.values]"))
        document.extend(
            f"{name} = {_toml_string(value)}" for name, value in sorted(values.items())
        )
    return "\n".join((*document, ""))


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_private_atomic(path: Path, content: str) -> None:
    _ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


__all__ = ["CredentialConfigurationApplication", "PreparedCredential"]
