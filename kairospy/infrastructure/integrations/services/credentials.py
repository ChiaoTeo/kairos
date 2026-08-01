from __future__ import annotations

from pathlib import Path
from typing import Mapping
import tomllib

from kairospy.config import find_manifest_path


def credential_value(credential: str | None, suffix: str, *fallback_env: str) -> str | None:
    _ = fallback_env
    return _credential_file_value(credential, suffix)


def credential_exists(credential: str | None) -> bool:
    return credential is not None and bool(credential.strip()) and _credential_path(credential.strip()) is not None


def credential_env_prefix(credential: str | None) -> str | None:
    if credential is None:
        return None
    value = credential.strip()
    if ":" in value:
        raise ValueError("credential must be a credential id")
    if not value:
        return None
    normalized = "".join(char.upper() if char.isalnum() else "_" for char in value)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_") or None


def _credential_file_value(credential: str | None, suffix: str) -> str | None:
    if credential is None or not credential.strip():
        return None
    path = _credential_path(credential.strip())
    if path is None:
        return None
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    table = values.get("credential")
    if not isinstance(table, Mapping):
        return None
    for key in _field_keys(suffix):
        value = table.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _credential_path(credential_id: str) -> Path | None:
    try:
        manifest = find_manifest_path(Path.cwd())
    except Exception:
        manifest = None
    if manifest is not None:
        path = manifest.parent / "credentials" / f"{credential_id}.toml"
        if path.exists():
            return path
    current = Path.cwd().resolve()
    for root in (current, *current.parents):
        path = root / ".kairos" / "credentials" / f"{credential_id}.toml"
        if path.exists():
            return path
    return None


def _field_keys(suffix: str) -> tuple[str, ...]:
    key = suffix.strip().lower()
    aliases = {
        "api_key": ("api_key", "key"),
        "secret": ("api_secret", "secret"),
        "password": ("passphrase", "password"),
        "passphrase": ("passphrase", "password"),
        "private_key": ("private_key",),
    }
    return aliases.get(key, (key,))


__all__ = ["credential_env_prefix", "credential_exists", "credential_value"]
