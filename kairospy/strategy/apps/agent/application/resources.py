"""Workspace-owned resources used by launch-scoped Agent runtimes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import hashlib
import importlib
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any, Mapping, cast

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import Workspace

from .model_connections import (
    ModelProviderCatalogEntry,
    ModelProviderConnectionApplication,
)
from .model_resources import (
    AvailableModelApplication,
    ModelEndpointApplication,
    ModelResourceMigrationApplication,
)


@dataclass(frozen=True, slots=True)
class AgentResourceApplication:
    workspace: Workspace

    def model_endpoints(self) -> tuple[dict[str, object], ...]:
        return ModelEndpointApplication(self.workspace).list()

    def available_models(self) -> tuple[dict[str, object], ...]:
        return AvailableModelApplication(self.workspace).list()

    def model_endpoint(self, endpoint_id: str) -> dict[str, object]:
        return ModelEndpointApplication(self.workspace).show(endpoint_id)

    def available_model(self, model_id: str) -> dict[str, object]:
        return AvailableModelApplication(self.workspace).show(model_id)

    def configure_model_endpoint(
        self, endpoint_id: str, **values: Any
    ) -> dict[str, object]:
        return ModelEndpointApplication(self.workspace).configure(endpoint_id, **values)

    def configure_available_model(
        self, model_id: str, **values: Any
    ) -> dict[str, object]:
        return AvailableModelApplication(self.workspace).configure(model_id, **values)

    def test_available_model(
        self, model_id: str, *, probe: Callable[..., object] | None = None
    ) -> dict[str, object]:
        return AvailableModelApplication(self.workspace).test(model_id, probe=probe)

    def converse_with_available_model(
        self,
        model_id: str,
        message: str,
        *,
        probe: Callable[..., object] | None = None,
    ) -> dict[str, object]:
        return AvailableModelApplication(self.workspace).converse(
            model_id, message, probe=probe
        )

    def migrate_legacy_model_resources(self) -> dict[str, object]:
        return ModelResourceMigrationApplication(self.workspace).migrate_legacy()

    def credential_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                [
                    str(value["credential_id"])
                    for value in self.model_endpoints()
                    if value.get("credential_id") and value.get("configured") is True
                ]
                + [
                    str(value["credential_id"])
                    for value in self.model_connections()
                    if value.get("credential_id") and value.get("configured") is True
                ]
            )
        )

    def model_connections(self) -> tuple[dict[str, object], ...]:
        configured = list(ModelProviderConnectionApplication(self.workspace).list())
        configured_ids = {str(value["connection_id"]) for value in configured}
        credentials = CredentialConfigurationApplication(self.workspace)
        # Preserve read compatibility for workspaces that predate explicit model
        # connection records. The first edit or test materializes the new record.
        for credential in credentials.list():
            if (
                credential.get("provider") != "openai"
                or str(credential["credential_id"]) in configured_ids
            ):
                continue
            credential_id = str(credential["credential_id"])
            configured.append(
                {
                    "connection_id": credential_id,
                    "provider": "openai",
                    "provider_label": "OpenAI",
                    "api_mode": "openai-responses",
                    "base_url": "https://api.openai.com/v1",
                    "credential_id": credential_id,
                    "models": [],
                    "configured": credential.get("configured") is True,
                    "issues": list(cast(Any, credential.get("issues", ()))),
                    **self.model_verification(credential_id),
                }
            )
        return tuple(sorted(configured, key=lambda value: str(value["connection_id"])))

    def provider_catalog(self) -> tuple[ModelProviderCatalogEntry, ...]:
        return ModelProviderConnectionApplication(self.workspace).provider_catalog()

    def model_connection(self, connection_id: str) -> dict[str, object]:
        try:
            return ModelProviderConnectionApplication(self.workspace).show(
                connection_id
            )
        except KeyError:
            for value in self.model_connections():
                if value.get("connection_id") == connection_id:
                    return value
            raise

    def verified_model_refs(self) -> tuple[dict[str, object], ...]:
        """Return concrete models currently safe for Launch selection."""

        result: list[dict[str, object]] = [
            {
                "model_ref": str(value["model_id"]),
                "model_id": value["model_id"],
                "endpoint_id": value["endpoint_id"],
                "provider_model": value["provider_model"],
                "provider": ModelEndpointApplication(self.workspace)
                .show(str(value["endpoint_id"]))
                .get("provider"),
                "provider_label": ModelEndpointApplication(self.workspace)
                .show(str(value["endpoint_id"]))
                .get("provider_label"),
                "last_tested_at": value.get("last_tested_at"),
            }
            for value in self.available_models()
            if value.get("configured") is True
            and value.get("verification_status") == "verified"
        ]
        connections = ModelProviderConnectionApplication(self.workspace)
        for connection in self.model_connections():
            if connection.get("configured") is not True:
                continue
            connection_id = str(connection["connection_id"])
            for model in _sequence(connection.get("verified_models")):
                model_id = str(model)
                verification = connections.verification(connection_id, model=model_id)
                if verification.get("verification_status") != "verified":
                    continue
                result.append(
                    {
                        "connection_id": connection_id,
                        "model": model_id,
                        "model_ref": f"{connection_id}/{model_id}",
                        "provider": connection.get("provider"),
                        "provider_label": connection.get("provider_label"),
                        "last_tested_at": verification.get("last_tested_at"),
                    }
                )
        return tuple(
            sorted(
                result,
                key=lambda value: str(value["model_ref"]),
            )
        )

    def available_model_snapshot(self, model_id: str) -> dict[str, object]:
        return AvailableModelApplication(self.workspace).resource_snapshot(model_id)

    def detect_local_model_providers(
        self,
        *,
        probe: Callable[[str, str], tuple[Mapping[str, object], ...]] | None = None,
    ) -> tuple[dict[str, object], ...]:
        return ModelProviderConnectionApplication(self.workspace).detect_local(
            probe=probe
        )

    def configure_model_connection(
        self,
        connection_id: str,
        *,
        provider: str,
        api_mode: str | None = None,
        base_url: str | None = None,
        credential_id: str | None = None,
        models: tuple[str, ...] = (),
        timeout_seconds: float = 60.0,
        enabled: bool = True,
        overwrite: bool = False,
    ) -> dict[str, object]:
        return ModelProviderConnectionApplication(self.workspace).configure(
            connection_id,
            provider=provider,
            api_mode=api_mode,
            base_url=base_url,
            credential_id=credential_id,
            models=models,
            timeout_seconds=timeout_seconds,
            enabled=enabled,
            overwrite=overwrite,
        )

    def set_model_connection_enabled(
        self, connection_id: str, *, enabled: bool
    ) -> dict[str, object]:
        return ModelProviderConnectionApplication(self.workspace).set_enabled(
            connection_id, enabled=enabled
        )

    def delete_model_connection(self, connection_id: str) -> dict[str, str]:
        return ModelProviderConnectionApplication(self.workspace).delete(connection_id)

    def discover_models(
        self,
        connection_id: str,
        *,
        probe: Callable[
            [Mapping[str, object], str | None], tuple[Mapping[str, object], ...]
        ]
        | None = None,
    ) -> tuple[dict[str, object], ...]:
        return ModelProviderConnectionApplication(self.workspace).discover_models(
            connection_id, probe=probe
        )

    def refresh_model_catalog(
        self,
        connection_id: str,
        *,
        probe: Callable[
            [Mapping[str, object], str | None], tuple[Mapping[str, object], ...]
        ]
        | None = None,
    ) -> dict[str, object]:
        """Discover and persist the current model IDs for one saved connection."""

        connections = ModelProviderConnectionApplication(self.workspace)
        current = connections.show(connection_id)
        discovered = connections.discover_models(connection_id, probe=probe)
        return connections.configure(
            connection_id,
            provider=str(current["provider"]),
            api_mode=str(current["api_mode"]),
            base_url=str(current["base_url"]),
            credential_id=(
                str(current["credential_id"])
                if current.get("credential_id") is not None
                else None
            ),
            models=tuple(str(value["id"]) for value in discovered),
            timeout_seconds=float(str(current.get("timeout_seconds") or 60.0)),
            enabled=bool(current.get("enabled", True)),
            overwrite=True,
        )

    def test_model_connection(
        self,
        connection_id: str,
        model: str,
        *,
        probe: Callable[[Mapping[str, object], str | None, str], object] | None = None,
    ) -> dict[str, object]:
        return ModelProviderConnectionApplication(self.workspace).test(
            connection_id, model, probe=probe
        )

    def converse_with_model(
        self,
        connection_id: str,
        model: str,
        message: str,
        *,
        probe: Callable[[Mapping[str, object], str | None, str, str], object]
        | None = None,
    ) -> dict[str, object]:
        return ModelProviderConnectionApplication(self.workspace).converse(
            connection_id, model, message, probe=probe
        )

    def probe_model_connection(
        self,
        connection: Mapping[str, object],
        model: str,
        *,
        secret: str | None,
        probe: Callable[[Mapping[str, object], str | None, str], object] | None = None,
    ) -> dict[str, object]:
        return ModelProviderConnectionApplication(self.workspace).probe(
            connection, model, secret=secret, probe=probe
        )

    def record_model_probe(
        self,
        connection_id: str,
        model: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        return ModelProviderConnectionApplication(self.workspace).record_probe(
            connection_id, model, result
        )

    def profile_ids(self) -> tuple[str, ...]:
        root = self.workspace.paths.agent_profiles_root()
        result: list[str] = []
        for path in sorted(root.glob("*.toml")) if root.is_dir() else ():
            try:
                value = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                continue
            profile = value.get("profile", value)
            if not isinstance(profile, Mapping):
                continue
            profile_id = profile.get("id", path.stem)
            if (
                isinstance(profile_id, str)
                and _safe_id(profile_id)
                and _valid_profile(profile)
            ):
                result.append(profile_id)
        return tuple(dict.fromkeys(result))

    def mcp_selections(self) -> tuple[tuple[str, str], ...]:
        path = self.workspace.paths.agent_mcp_config()
        if not path.is_file():
            return ()
        try:
            value = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return ()
        servers = value.get("servers")
        profiles = value.get("profiles")
        if not isinstance(servers, Mapping) or not isinstance(profiles, Mapping):
            return ()
        result: list[tuple[str, str]] = []
        for profile_id, profile in profiles.items():
            if not isinstance(profile_id, str) or not isinstance(profile, Mapping):
                continue
            server_id = profile.get("server")
            if (
                isinstance(server_id, str)
                and server_id in servers
                and _safe_id(server_id)
                and _safe_id(profile_id)
            ):
                result.append((server_id, profile_id))
        return tuple(sorted(result))

    def status(self) -> dict[str, object]:
        credentials = self.credential_ids()
        profiles = self.profile_ids()
        selections = self.mcp_selections()
        endpoints = self.model_endpoints()
        models = self.available_models()
        verified = self.verified_model_refs()
        return {
            "ready": bool(verified),
            "credentials": list(credentials),
            "model_connections": list(self.model_connections()),
            "model_endpoints": list(endpoints),
            "available_models": list(models),
            "profiles": list(profiles),
            "mcp": [
                {"server": server_id, "profile": profile_id}
                for server_id, profile_id in selections
            ],
            "legacy_workspace_profiles": bool(profiles or selections),
            "next_steps": [] if verified else ["kairos config agent setup"],
        }

    def configure_openai_credential(
        self,
        credential_id: str,
        api_key: str,
        *,
        overwrite: bool = False,
    ) -> dict[str, object]:
        result = CredentialConfigurationApplication(self.workspace).configure(
            credential_id,
            provider="openai",
            role="model-inference",
            values={"api_key": api_key},
            overwrite=overwrite,
        )
        connections = ModelProviderConnectionApplication(self.workspace)
        try:
            connections.show(credential_id)
            connection_exists = True
        except KeyError:
            connection_exists = False
        connections.configure(
            credential_id,
            provider="openai",
            credential_id=credential_id,
            overwrite=connection_exists,
        )
        return result

    def test_openai_model(
        self,
        credential_id: str,
        model: str,
        *,
        probe: Callable[[str, str], object] | None = None,
    ) -> dict[str, object]:
        credential_id = _resource_id(credential_id, "OpenAI credential")
        model = _required_text(model, "OpenAI model")
        connections = ModelProviderConnectionApplication(self.workspace)
        try:
            connections.show(credential_id)
        except KeyError:
            connections.configure(
                credential_id,
                provider="openai",
                credential_id=credential_id,
            )
        adapted = (
            None
            if probe is None
            else lambda _connection, api_key, selected_model: probe(
                str(api_key or ""), selected_model
            )
        )
        return connections.test(credential_id, model, probe=adapted)

    def model_verification(
        self, credential_id: str, *, model: str | None = None
    ) -> dict[str, object]:
        connections = ModelProviderConnectionApplication(self.workspace)
        try:
            connections.show(credential_id)
        except KeyError:
            pass
        else:
            return connections.verification(credential_id, model=model)
        credentials = CredentialConfigurationApplication(self.workspace)
        try:
            credential = credentials.show(credential_id)
        except KeyError:
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested_configuration_hash": None,
                "current_configuration_hash": None,
                "tested": [],
                "not_tested": [],
                "capabilities": [],
            }
        path = self._model_verification_path(credential_id)
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested_configuration_hash": None,
                "current_configuration_hash": _model_configuration_hash(
                    credential, model
                ),
                "tested": [],
                "not_tested": [],
                "capabilities": [],
            }
        if not isinstance(evidence, Mapping):
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested_configuration_hash": None,
                "current_configuration_hash": _model_configuration_hash(
                    credential, model
                ),
                "tested": [],
                "not_tested": [],
                "capabilities": [],
            }
        changed = evidence.get("credential_hash") != _credential_hash(credential)
        if model is not None and evidence.get("model") != model:
            changed = True
        status = (
            "retest_required"
            if changed
            else "verified"
            if evidence.get("succeeded") is True
            else "failed"
        )
        return {
            "verification_status": status,
            "model": evidence.get("model"),
            "last_tested_at": evidence.get("tested_at"),
            "last_test_detail": evidence.get("detail"),
            "tested_configuration_hash": _model_evidence_hash(evidence),
            "current_configuration_hash": _model_configuration_hash(
                credential, model or str(evidence.get("model") or "")
            ),
            "tested": list(evidence.get("tested") or ()),
            "not_tested": list(evidence.get("not_tested") or ()),
            "capabilities": list(evidence.get("capabilities") or ()),
        }

    def resource_snapshot(self, credential_id: str, *, model: str) -> dict[str, object]:
        connections = ModelProviderConnectionApplication(self.workspace)
        try:
            connection = connections.resource_snapshot(credential_id, model=model)
        except KeyError:
            connection = None
        credential = CredentialConfigurationApplication(self.workspace).show(
            credential_id
        )
        if connection is not None:
            return {
                **connection,
                "credential_identity": {
                    "provider": credential.get("provider"),
                    "role": credential.get("role"),
                    "fields": credential.get("fields", []),
                },
            }
        verification = self.model_verification(credential_id, model=model)
        payload: dict[str, object] = {
            "connection_id": credential_id,
            "provider": "openai",
            "credential_id": credential_id,
            "model": model,
            "credential_identity": {
                "provider": credential.get("provider"),
                "role": credential.get("role"),
                "fields": credential.get("fields", []),
            },
            "verification": verification,
        }
        payload["resource_hash"] = hashlib.sha256(
            json.dumps(
                {
                    "credential_hash": _credential_hash(credential),
                    "model": model,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return payload

    def _model_verification_path(self, credential_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "models", f"{credential_id}.json"
        )

    def create_openai_credential(
        self, credential_id: str, api_key: str, *, overwrite: bool = False
    ) -> Path:
        self.configure_openai_credential(credential_id, api_key, overwrite=overwrite)
        return self.workspace.paths.credentials_root() / f"{credential_id}.toml"

    def create_profile(
        self,
        profile_id: str,
        *,
        goal: str,
        rubric: tuple[str, ...],
        invalidation_rules: tuple[str, ...],
        reason_codes: tuple[str, ...] = (),
        risk_flags: tuple[str, ...] = (),
        version: str = "1",
        overwrite: bool = False,
    ) -> Path:
        del profile_id, goal, rubric, invalidation_rules, reason_codes
        del risk_flags, version, overwrite
        raise ValueError("Agent Profile is Launch-owned; edit it inline in a Launch")

    def configure_mcp(
        self,
        server_id: str,
        profile_id: str,
        *,
        transport: str,
        allowed_tools: tuple[str, ...],
        command: str | None = None,
        args: tuple[str, ...] = (),
        url: str | None = None,
        credential: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> Path:
        del server_id, profile_id, transport, allowed_tools, command, args
        del url, credential, timeout_seconds
        raise ValueError("Agent MCP and tool policy are Launch-owned; edit them inline")


def _write_private_atomic(path: Path, content: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _credential_hash(value: Mapping[str, object]) -> str:
    payload = {
        "credential_id": value.get("credential_id"),
        "provider": value.get("provider"),
        "role": value.get("role"),
        "fields": value.get("fields"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sequence(value: object) -> Sequence[object]:
    """Validate a sequence read from a workspace resource record."""

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _model_configuration_hash(
    credential: Mapping[str, object], model: str | None
) -> str:
    return hashlib.sha256(
        json.dumps(
            {"credential_hash": _credential_hash(credential), "model": model or ""},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _model_evidence_hash(evidence: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "credential_hash": evidence.get("credential_hash"),
                "model": evidence.get("model"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _probe_openai_model(api_key: str, model: str) -> object:
    try:
        sdk = importlib.import_module("openai")
    except ImportError as error:
        raise RuntimeError(
            "OpenAI model testing requires the optional 'agent' dependency group"
        ) from error
    client = sdk.OpenAI(api_key=api_key)
    return client.responses.create(
        model=model,
        input="Reply with OK to verify this Kairos model connection.",
        max_output_tokens=8,
        store=False,
    )


def _safe_id(value: str) -> bool:
    return bool(value) and all(
        character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in value
    )


def _valid_profile(value: Mapping[object, object]) -> bool:
    if (
        not isinstance(value.get("version"), str)
        or not str(value.get("version")).strip()
    ):
        return False
    if not isinstance(value.get("goal"), str) or not str(value.get("goal")).strip():
        return False
    for key in ("rubric", "invalidation_rules"):
        items = value.get(key)
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(item, str) or not item.strip() for item in items)
        ):
            return False
    return True


def _resource_id(value: str, name: str) -> str:
    result = value.strip()
    if not _safe_id(result):
        raise ValueError(f"{name} id must contain only letters, digits, '_' or '-'")
    return result


def _required_text(value: str, name: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _items(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if len(result) != len(tuple(value for value in values if value.strip())):
        # Duplicate entries are harmless but usually indicate a mistaken setup.
        result = tuple(dict.fromkeys(result))
    if any("\n" in value or "\r" in value for value in result):
        raise ValueError(f"{name} entries must be single-line strings")
    return result


def _required_items(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    result = _items(values, name)
    if not result:
        raise ValueError(f"{name} requires at least one entry")
    return result


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _mcp_document(values: Mapping[str, object]) -> str:
    lines: list[str] = []
    for group in ("servers", "profiles"):
        entries = values.get(group, {})
        if not isinstance(entries, Mapping):
            raise ValueError(f"Agent MCP {group} must be a table")
        for resource_id, resource in sorted(entries.items()):
            if not isinstance(resource_id, str) or not isinstance(resource, Mapping):
                raise ValueError(f"Agent MCP {group} entries must be tables")
            _resource_id(resource_id, f"Agent MCP {group}")
            if lines:
                lines.append("")
            lines.append(f"[{group}.{resource_id}]")
            for key, value in resource.items():
                if not isinstance(key, str) or not _safe_id(key):
                    raise ValueError(f"Agent MCP field is invalid: {key}")
                lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    ):
        return _toml_array(tuple(value))
    raise ValueError(f"unsupported Agent MCP configuration value: {value!r}")


__all__ = ["AgentResourceApplication"]
