from __future__ import annotations

"""System-facing credential administration adapter."""

from pathlib import Path
from typing import Mapping, Sequence

from kairospy.application.usecases.account.application.schemas import ACCOUNT_SCHEMAS
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.application.usecases.workspace.domain.workspace import CredentialRecord, write_credential_file
from kairospy.application.usecases.workspace.domain.config import ConfigError


class CredentialAdminApplication:
    def list_credentials(self) -> dict[str, object]:
        store = resolve_workspace().credentials
        return {"credentials": [record.to_dict() for record in store.list()], "count": len(store.list()), "root": str(store.root)}

    def create(
        self,
        *,
        credential_id: str,
        broker: str,
        kind: str | None,
        api_key: str | None,
        api_secret: str | None,
        passphrase: str | None,
        password: str | None,
        wallet_address: str | None,
        private_key: str | None,
        vault_address: str | None,
        field_values: Sequence[str] | None,
        force: bool,
    ) -> str:
        workspace = resolve_workspace()
        broker_value = _normalize_broker(broker)
        schema = ACCOUNT_SCHEMAS.get(broker_value)
        resolved_kind = kind or (schema.credential_kind if schema is not None else None)
        values = {
            "id": credential_id,
            "broker": broker_value,
            "kind": resolved_kind,
            "api_key": api_key,
            "api_secret": api_secret,
            "passphrase": passphrase or password,
            "wallet_address": wallet_address,
            "private_key": private_key,
            "vault_address": vault_address,
            **_pairs(field_values),
        }
        _require_secret_value(values)
        path = workspace.credentials_root / f"{credential_id}.toml"
        if path.exists() and not force:
            raise ValueError(f"credential already exists: {path}")
        write_credential_file(path, values)
        workspace.operations.append("credential.create", target={"credential": credential_id}, payload={"path": path, "broker": broker_value})
        return str(path)

    def show(self, credential_id: str, *, reveal_secrets: bool) -> dict[str, object]:
        credential = _credential(credential_id)
        return credential.to_dict(include_secret_values=reveal_secrets)

    def delete(self, credential_id: str, *, force: bool) -> str:
        _ = force
        workspace = resolve_workspace()
        credential = _credential(credential_id)
        path = credential.source_path or workspace.credentials_root / f"{credential.credential_id}.toml"
        if path.parent != workspace.credentials_root:
            raise ValueError(f"refusing to delete credential outside credentials root: {path}")
        path.unlink()
        workspace.operations.append("credential.delete", target={"credential": credential.credential_id}, payload={"path": path})
        return str(path)


def _credential(credential_id: str) -> CredentialRecord:
    try:
        return resolve_workspace().credentials.get(credential_id)
    except ConfigError as error:
        raise ValueError(str(error)) from error


def _normalize_broker(broker: str) -> str:
    normalized = broker.strip().lower().replace("-", "_")
    return {"okex": "okx", "ouyi": "okx"}.get(normalized, normalized)


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


def _require_secret_value(values: Mapping[str, object]) -> None:
    if any(values.get(key) not in {None, ""} for key in ("api_key", "api_secret", "passphrase", "wallet_address", "private_key")):
        return
    raise ValueError("credential create requires at least one secret or credential field")


__all__ = ["CredentialAdminApplication"]
