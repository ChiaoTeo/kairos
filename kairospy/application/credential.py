"""Workspace credential configuration owned by the Integration capability."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Literal, Mapping

from kairospy.application.workspace import Workspace


SecretSource = Literal["env", "file"]


@dataclass(frozen=True, slots=True)
class SecretRef:
    source: SecretSource
    id: str

    def __post_init__(self) -> None:
        identifier = self.id.strip()
        if self.source == "env":
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", identifier):
                raise ValueError(
                    "environment SecretRef id must use uppercase letters, numbers, and underscores"
                )
        elif self.source == "file":
            if not identifier or "\x00" in identifier:
                raise ValueError("file SecretRef id must be a non-empty path")
        else:
            raise ValueError(f"unsupported SecretRef source: {self.source}")
        object.__setattr__(self, "id", identifier)


_PROVIDER_FIELDS: Mapping[str, tuple[str, ...]] = {
    "paper": (),
    "simulated": (),
    "ibkr": (),
    "massive": ("api_key",),
    "binance": ("api_key", "api_secret"),
    "okx": ("api_key", "api_secret", "passphrase"),
    "openai": ("api_key",),
    "feishu": ("webhook_url",),
    "telegram": ("bot_token",),
}


@dataclass(frozen=True, slots=True)
class CredentialConfigurationApplication:
    """Configure SecretRef metadata without reading or writing plaintext secrets."""

    workspace: Workspace

    @property
    def root(self) -> Path:
        return self.workspace.paths.credential_config().parent

    def schema(self, provider: str) -> dict[str, object]:
        provider = provider.strip().lower()
        fields = _PROVIDER_FIELDS.get(provider)
        if fields is None:
            raise ValueError(f"unsupported credential provider: {provider}")
        return {"provider": provider, "required_fields": list(fields)}

    def default_environment(self, credential_id: str, field: str) -> str:
        credential_id = _safe_id(credential_id, "credential_id")
        field = _field_name(field)
        prefix = re.sub(r"[^A-Za-z0-9]", "_", credential_id).upper()
        return f"KAIROS_CREDENTIAL_{prefix}_{field.upper()}"

    def configure(
        self,
        credential_id: str,
        *,
        provider: str,
        fields: Mapping[str, SecretRef],
        role: str = "readonly",
        overwrite: bool = False,
    ) -> dict[str, object]:
        credential_id = _safe_id(credential_id, "credential_id")
        provider = provider.strip().lower()
        schema = self.schema(provider)
        normalized = {
            _field_name(name): reference for name, reference in fields.items()
        }
        missing = [name for name in schema["required_fields"] if name not in normalized]
        if missing:
            raise ValueError(
                f"credential {credential_id} is missing required fields: {', '.join(missing)}"
            )
        path = self.root / f"{credential_id}.toml"
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        document = [
            "[credential]",
            f"id = {_toml_string(credential_id)}",
            f"provider = {_toml_string(provider)}",
            f"role = {_toml_string(role.strip() or 'readonly')}",
        ]
        for name, reference in sorted(normalized.items()):
            document.extend(
                (
                    "",
                    f"[credential.fields.{name}]",
                    f"source = {_toml_string(reference.source)}",
                    f"id = {_toml_string(reference.id)}",
                )
            )
        _write_private_atomic(path, "\n".join((*document, "")))
        return self.show(credential_id)

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
                        "legacy_plaintext": False,
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
        return {"credential_id": credential_id, "status": "deleted"}

    def resource_snapshot(self, credential_id: str) -> dict[str, object]:
        """Return SecretRef identity and a hash that never includes secret values."""

        summary = self.show(credential_id)
        identity = {
            "credential_id": credential_id,
            "provider": summary.get("provider"),
            "role": summary.get("role"),
            "secret_refs": summary.get("secret_refs", {}),
            "legacy_plaintext": summary.get("legacy_plaintext", False),
        }
        return {
            **identity,
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
        path = self.root / f"{credential_id}.toml"
        value = _credential_table(path)
        fields = value.get("fields")
        if isinstance(fields, Mapping):
            raw_reference = fields.get(field)
            if isinstance(raw_reference, Mapping):
                source = raw_reference.get("source")
                identifier = raw_reference.get("id")
                if isinstance(source, str) and isinstance(identifier, str):
                    return self.resolve(SecretRef(source, identifier))  # type: ignore[arg-type]
        legacy = value.get(field)
        if isinstance(legacy, str) and legacy.strip():
            return legacy.strip()
        return None

    def resolve(self, reference: SecretRef) -> str | None:
        if reference.source == "env":
            return os.environ.get(reference.id, "").strip() or None
        path = Path(reference.id).expanduser()
        if not path.is_absolute():
            path = self.workspace.paths.root / path
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def _summary(self, path: Path) -> dict[str, object]:
        value = _credential_table(path)
        credential_id = _safe_id(str(value.get("id", path.stem)), "credential_id")
        provider = str(value.get("provider", value.get("broker", ""))).lower()
        expected = _PROVIDER_FIELDS.get(provider, ())
        fields_value = value.get("fields")
        fields = fields_value if isinstance(fields_value, Mapping) else {}
        references: dict[str, dict[str, str]] = {}
        available: dict[str, bool] = {}
        issues: list[str] = []
        for name, raw_reference in fields.items():
            if not isinstance(name, str) or not isinstance(raw_reference, Mapping):
                issues.append("credential field reference is invalid")
                continue
            source = raw_reference.get("source")
            identifier = raw_reference.get("id")
            if not isinstance(source, str) or not isinstance(identifier, str):
                issues.append(f"credential field {name} requires source and id")
                continue
            try:
                reference = SecretRef(source, identifier)  # type: ignore[arg-type]
            except ValueError as error:
                issues.append(f"credential field {name}: {error}")
                continue
            references[name] = {"source": reference.source, "id": reference.id}
            available[name] = self.resolve(reference) is not None
        legacy_fields = [
            name
            for name in (
                *expected,
                "api_key",
                "api_secret",
                "passphrase",
                "bot_token",
                "webhook_url",
            )
            if isinstance(value.get(name), str) and str(value[name]).strip()
        ]
        missing = [
            name
            for name in expected
            if name not in references and name not in legacy_fields
        ]
        if missing:
            issues.append(f"missing required fields: {', '.join(missing)}")
        unavailable = [
            name for name in expected if name in references and not available[name]
        ]
        if unavailable:
            issues.append(f"SecretRef unavailable: {', '.join(unavailable)}")
        if legacy_fields:
            issues.append("legacy plaintext credential requires migration")
        return {
            "credential_id": credential_id,
            "provider": provider,
            "role": str(value.get("role", "readonly")),
            "configured": not missing and not unavailable and not legacy_fields,
            "secret_refs": references,
            "field_available": available,
            "legacy_plaintext": bool(legacy_fields),
            "issues": issues,
        }


def _credential_table(path: Path) -> Mapping[str, object]:
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    credential = value.get("credential", value)
    if not isinstance(credential, Mapping):
        raise ValueError(f"credential TOML root is not a table: {path}")
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
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_private_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "CredentialConfigurationApplication",
    "SecretRef",
    "SecretSource",
]
