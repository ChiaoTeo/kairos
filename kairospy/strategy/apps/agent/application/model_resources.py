"""Model Endpoint and Available Model workspace resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any, cast

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import (
    Workspace,
    WorkspaceConfigurationTransaction,
)

from .model_connections import (
    CatalogProbe,
    ConversationProbe,
    ModelProbe,
    ModelProviderCatalogEntry,
    ModelProviderConnectionApplication,
)


@dataclass(frozen=True, slots=True)
class PreparedModelEndpoint:
    workspace: Workspace
    endpoint: Mapping[str, object]
    document: str

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        transaction.stage_text(
            self.workspace.paths.model_endpoints_root()
            / f"{self.endpoint['endpoint_id']}.toml",
            self.document,
        )


@dataclass(frozen=True, slots=True)
class PreparedAvailableModel:
    workspace: Workspace
    model: Mapping[str, object]
    document: str

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        transaction.stage_text(
            self.workspace.paths.available_models_root()
            / f"{self.model['model_id']}.toml",
            self.document,
        )


@dataclass(frozen=True, slots=True)
class ModelEndpointApplication:
    workspace: Workspace

    def provider_catalog(self) -> tuple[ModelProviderCatalogEntry, ...]:
        return ModelProviderConnectionApplication(self.workspace).provider_catalog()

    def prepare(
        self,
        endpoint_id: str,
        *,
        provider: str,
        api_mode: str | None = None,
        base_url: str | None = None,
        credential_id: str | None = None,
        credential_provider: str | None = None,
        timeout_seconds: float = 60.0,
        enabled: bool = True,
    ) -> PreparedModelEndpoint:
        legacy = ModelProviderConnectionApplication(self.workspace).prepare(
            endpoint_id,
            provider=provider,
            api_mode=api_mode,
            base_url=base_url,
            credential_id=credential_id,
            credential_provider=credential_provider,
            timeout_seconds=timeout_seconds,
            enabled=enabled,
        )
        endpoint = _endpoint_from_connection(legacy.connection)
        return PreparedModelEndpoint(
            self.workspace,
            endpoint,
            _endpoint_document(endpoint),
        )

    def configure(
        self,
        endpoint_id: str,
        *,
        provider: str,
        api_mode: str | None = None,
        base_url: str | None = None,
        credential_id: str | None = None,
        timeout_seconds: float = 60.0,
        enabled: bool = True,
        overwrite: bool = False,
    ) -> dict[str, object]:
        path = self._path(_safe_id(endpoint_id, "endpoint_id"))
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        prepared = self.prepare(
            endpoint_id,
            provider=provider,
            api_mode=api_mode,
            base_url=base_url,
            credential_id=credential_id,
            timeout_seconds=timeout_seconds,
            enabled=enabled,
        )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"model-endpoint:{endpoint_id}"
        )
        prepared.stage(transaction)
        transaction.commit()
        return self.show(endpoint_id)

    def list(self) -> tuple[dict[str, object], ...]:
        root = self.workspace.paths.model_endpoints_root()
        if not root.is_dir():
            return ()
        values: list[dict[str, object]] = []
        for path in sorted(root.glob("*.toml")):
            try:
                values.append(self._read(path))
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                values.append(
                    {
                        "endpoint_id": path.stem,
                        "configured": False,
                        "issues": ["模型服务端点配置无效"],
                    }
                )
        return tuple(values)

    def show(self, endpoint_id: str) -> dict[str, object]:
        path = self._path(_safe_id(endpoint_id, "endpoint_id"))
        if not path.is_file():
            raise KeyError(f"model endpoint does not exist: {endpoint_id}")
        return self._read(path)

    def set_enabled(self, endpoint_id: str, *, enabled: bool) -> dict[str, object]:
        current = self.show(endpoint_id)
        return self.configure(
            endpoint_id,
            provider=str(current["provider"]),
            api_mode=str(current["api_mode"]),
            base_url=str(current["base_url"]),
            credential_id=_optional_text(current.get("credential_id")),
            timeout_seconds=float(str(current.get("timeout_seconds") or 60.0)),
            enabled=enabled,
            overwrite=True,
        )

    def delete(self, endpoint_id: str, *, force: bool = False) -> dict[str, str]:
        endpoint_id = _safe_id(endpoint_id, "endpoint_id")
        path = self._path(endpoint_id)
        if not path.is_file():
            raise KeyError(f"model endpoint does not exist: {endpoint_id}")
        references = AvailableModelApplication(self.workspace).for_endpoint(endpoint_id)
        if references and not force:
            raise ValueError(
                f"model endpoint is referenced by {len(references)} models"
            )
        path.unlink()
        return {"endpoint_id": endpoint_id, "status": "deleted"}

    def discover_models(
        self, endpoint_id: str, *, probe: CatalogProbe | None = None
    ) -> tuple[dict[str, object], ...]:
        endpoint = self.show(endpoint_id)
        return ModelProviderConnectionApplication(self.workspace).discover(
            _connection_from_endpoint(endpoint),
            secret=self._secret(endpoint),
            probe=probe,
        )

    def resource_hash(self, endpoint: Mapping[str, object]) -> str:
        credential_id = _optional_text(endpoint.get("credential_id"))
        credential_hash: str | None = None
        if credential_id is not None:
            try:
                credential_hash = str(
                    CredentialConfigurationApplication(
                        self.workspace
                    ).resource_snapshot(credential_id)["resource_hash"]
                )
            except (KeyError, OSError, ValueError):
                credential_hash = "unavailable"
        payload = {
            "endpoint_id": endpoint.get("endpoint_id"),
            "provider": endpoint.get("provider"),
            "api_mode": endpoint.get("api_mode"),
            "base_url": endpoint.get("base_url"),
            "credential_id": credential_id,
            "credential_hash": credential_hash,
            "timeout_seconds": endpoint.get("timeout_seconds"),
            "enabled": endpoint.get("enabled", True),
        }
        return _hash(payload)

    def _read(self, path: Path) -> dict[str, object]:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        raw = value.get("endpoint", value)
        if not isinstance(raw, Mapping):
            raise ValueError("model endpoint root must be a table")
        endpoint_id = _safe_id(str(raw.get("id") or path.stem), "endpoint_id")
        provider = str(raw.get("provider") or "").strip().lower()
        api_mode = str(raw.get("api_mode") or "").strip()
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        credential_id = _optional_text(raw.get("credential_id"))
        enabled = bool(raw.get("enabled", True))
        issues: list[str] = []
        legacy = ModelProviderConnectionApplication(self.workspace)
        try:
            legacy.prepare(
                endpoint_id,
                provider=provider,
                api_mode=api_mode,
                base_url=base_url,
                credential_id=credential_id,
                timeout_seconds=float(raw.get("timeout_seconds") or 60.0),
                enabled=enabled,
            )
        except (KeyError, OSError, ValueError) as error:
            issues.append(str(error))
        result: dict[str, object] = {
            "endpoint_id": endpoint_id,
            "provider": provider,
            "provider_label": _provider_label(legacy, provider),
            "api_mode": api_mode,
            "base_url": base_url,
            "credential_id": credential_id,
            "timeout_seconds": float(raw.get("timeout_seconds") or 60.0),
            "enabled": enabled,
            "configured": enabled and not issues,
            "issues": issues,
        }
        result["resource_hash"] = self.resource_hash(result)
        return result

    def _secret(self, endpoint: Mapping[str, object]) -> str | None:
        credential_id = _optional_text(endpoint.get("credential_id"))
        if credential_id is None:
            return None
        return CredentialConfigurationApplication(self.workspace).resolve_field(
            credential_id, "api_key"
        )

    def _path(self, endpoint_id: str) -> Path:
        return self.workspace.paths.model_endpoints_root() / f"{endpoint_id}.toml"


@dataclass(frozen=True, slots=True)
class AvailableModelApplication:
    workspace: Workspace

    def prepare(
        self,
        model_id: str,
        *,
        endpoint_id: str,
        provider_model: str,
        enabled: bool = True,
    ) -> PreparedAvailableModel:
        model_id = _safe_model_id(model_id)
        endpoint_id = _safe_id(endpoint_id, "endpoint_id")
        provider_model = _provider_model(provider_model)
        ModelEndpointApplication(self.workspace).show(endpoint_id)
        model = {
            "model_id": model_id,
            "endpoint_id": endpoint_id,
            "provider_model": provider_model,
            "enabled": enabled,
        }
        return PreparedAvailableModel(
            self.workspace, model, _available_model_document(model)
        )

    def configure(
        self,
        model_id: str,
        *,
        endpoint_id: str,
        provider_model: str,
        enabled: bool = True,
        overwrite: bool = False,
    ) -> dict[str, object]:
        path = self._path(_safe_model_id(model_id))
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        prepared = self.prepare(
            model_id,
            endpoint_id=endpoint_id,
            provider_model=provider_model,
            enabled=enabled,
        )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"available-model:{model_id}"
        )
        prepared.stage(transaction)
        transaction.commit()
        return self.show(model_id)

    def list(self) -> tuple[dict[str, object], ...]:
        root = self.workspace.paths.available_models_root()
        if not root.is_dir():
            return ()
        values: list[dict[str, object]] = []
        for path in sorted(root.glob("*.toml")):
            try:
                values.append(self._read(path))
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                values.append(
                    {
                        "model_id": path.stem,
                        "configured": False,
                        "verification_status": "pending",
                        "issues": ["可用模型配置无效"],
                    }
                )
        return tuple(values)

    def show(self, model_id: str) -> dict[str, object]:
        path = self._path(_safe_model_id(model_id))
        if not path.is_file():
            raise KeyError(f"available model does not exist: {model_id}")
        return self._read(path)

    def for_endpoint(self, endpoint_id: str) -> tuple[dict[str, object], ...]:
        endpoint_id = _safe_id(endpoint_id, "endpoint_id")
        return tuple(
            value for value in self.list() if value.get("endpoint_id") == endpoint_id
        )

    def set_enabled(self, model_id: str, *, enabled: bool) -> dict[str, object]:
        current = self.show(model_id)
        return self.configure(
            model_id,
            endpoint_id=str(current["endpoint_id"]),
            provider_model=str(current["provider_model"]),
            enabled=enabled,
            overwrite=True,
        )

    def delete(self, model_id: str) -> dict[str, str]:
        model_id = _safe_model_id(model_id)
        path = self._path(model_id)
        if not path.is_file():
            raise KeyError(f"available model does not exist: {model_id}")
        path.unlink()
        self._evidence_path(model_id).unlink(missing_ok=True)
        return {"model_id": model_id, "status": "deleted"}

    def test(
        self, model_id: str, *, probe: ModelProbe | None = None
    ) -> dict[str, object]:
        model, endpoint, secret = self._call_context(model_id)
        result = ModelProviderConnectionApplication(self.workspace).probe(
            _connection_from_endpoint(endpoint),
            str(model["provider_model"]),
            secret=secret,
            probe=probe,
        )
        return self.record_test(model_id, result)

    def converse(
        self,
        model_id: str,
        message: str,
        *,
        probe: ConversationProbe | None = None,
    ) -> dict[str, object]:
        model, endpoint, secret = self._call_context(model_id)
        result = ModelProviderConnectionApplication(self.workspace).converse_config(
            _connection_from_endpoint(endpoint),
            str(model["provider_model"]),
            message,
            secret=secret,
            probe=probe,
        )
        verification = self.record_test(model_id, result)
        return {
            **result,
            **verification,
            "model_id": model_id,
            "endpoint_id": model["endpoint_id"],
            "provider_model": model["provider_model"],
        }

    def record_test(
        self, model_id: str, result: Mapping[str, object]
    ) -> dict[str, object]:
        model = self.show(model_id)
        succeeded = result.get("succeeded") is True
        evidence = {
            "version": 1,
            "model_id": model_id,
            "endpoint_id": model.get("endpoint_id"),
            "provider_model": model.get("provider_model"),
            "configuration_hash": model.get("current_configuration_hash"),
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "succeeded": succeeded,
            "detail": str(result.get("detail") or "模型调用失败"),
            "error_category": result.get("error_category"),
            "tested": ["endpoint", "authentication", "minimum text response"],
            "not_tested": [
                "image input",
                "tool calls",
                "structured output",
                "streaming responses",
                "production throughput",
            ],
            "capabilities": ["text_inference"] if succeeded else [],
        }
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"available-model-verification:{model_id}"
        )
        transaction.stage_text(
            self._evidence_path(model_id),
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        )
        transaction.commit()
        return self.verification(model_id)

    def verification(self, model_id: str) -> dict[str, object]:
        model_id = _safe_model_id(model_id)
        try:
            model = self._base(self._path(model_id))
        except (FileNotFoundError, KeyError):
            return _pending(None, model_id)
        current_hash = self._configuration_hash(model)
        try:
            evidence = json.loads(
                self._evidence_path(model_id).read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return _pending(current_hash, model_id)
        if not isinstance(evidence, Mapping):
            return _pending(current_hash, model_id)
        status = (
            "retest_required"
            if evidence.get("configuration_hash") != current_hash
            else "verified"
            if evidence.get("succeeded") is True
            else "failed"
        )
        return {
            "verification_status": status,
            "model_id": model_id,
            "last_tested_at": evidence.get("tested_at"),
            "last_test_detail": evidence.get("detail"),
            "error_category": evidence.get("error_category"),
            "tested_configuration_hash": evidence.get("configuration_hash"),
            "current_configuration_hash": current_hash,
            "tested": list(_sequence(evidence.get("tested"))),
            "not_tested": list(_sequence(evidence.get("not_tested"))),
            "capabilities": list(_sequence(evidence.get("capabilities"))),
        }

    def resource_snapshot(self, model_id: str) -> dict[str, object]:
        """Return the immutable, secret-free identity captured by a Launch."""

        model = self.show(model_id)
        endpoint = ModelEndpointApplication(self.workspace).show(
            str(model["endpoint_id"])
        )
        return {
            "model_id": model["model_id"],
            "endpoint_id": model["endpoint_id"],
            "provider_model": model["provider_model"],
            "enabled": model.get("enabled", True),
            "endpoint": {
                "provider": endpoint.get("provider"),
                "api_mode": endpoint.get("api_mode"),
                "base_url": endpoint.get("base_url"),
                "credential_id": endpoint.get("credential_id"),
                "resource_hash": endpoint.get("resource_hash"),
            },
            "verification": self.verification(model_id),
            "resource_hash": model.get("current_configuration_hash"),
        }

    def _read(self, path: Path) -> dict[str, object]:
        model = self._base(path)
        endpoint_id = str(model["endpoint_id"])
        issues: list[str] = []
        try:
            endpoint = ModelEndpointApplication(self.workspace).show(endpoint_id)
        except (KeyError, OSError, ValueError):
            endpoint = None
            issues.append("模型服务端点不存在或无效")
        else:
            if endpoint.get("configured") is not True:
                issues.append("模型服务端点不可用")
        verification = self.verification(str(model["model_id"]))
        enabled = bool(model.get("enabled", True))
        if not enabled or (endpoint is not None and endpoint.get("enabled") is False):
            verification = {**verification, "verification_status": "disabled"}
        return {
            **model,
            "configured": enabled and not issues,
            "issues": issues,
            **verification,
        }

    def _base(self, path: Path) -> dict[str, object]:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        raw = value.get("model", value)
        if not isinstance(raw, Mapping):
            raise ValueError("available model root must be a table")
        model = {
            "model_id": _safe_model_id(str(raw.get("id") or path.stem)),
            "endpoint_id": _safe_id(str(raw.get("endpoint") or ""), "endpoint_id"),
            "provider_model": _provider_model(str(raw.get("provider_model") or "")),
            "enabled": bool(raw.get("enabled", True)),
        }
        model["current_configuration_hash"] = self._configuration_hash(model)
        return model

    def _configuration_hash(self, model: Mapping[str, object]) -> str:
        endpoint_id = str(model["endpoint_id"])
        try:
            endpoint = ModelEndpointApplication(self.workspace).show(endpoint_id)
            endpoint_hash = endpoint.get("resource_hash")
        except (KeyError, OSError, ValueError):
            endpoint_hash = "unavailable"
        return _hash(
            {
                "model_id": model.get("model_id"),
                "endpoint_id": endpoint_id,
                "provider_model": model.get("provider_model"),
                "enabled": model.get("enabled", True),
                "endpoint_hash": endpoint_hash,
            }
        )

    def _call_context(
        self, model_id: str
    ) -> tuple[dict[str, object], dict[str, object], str | None]:
        model = self.show(model_id)
        if model.get("configured") is not True:
            raise ValueError(f"available model is not ready: {model_id}")
        endpoint = ModelEndpointApplication(self.workspace).show(
            str(model["endpoint_id"])
        )
        credential_id = _optional_text(endpoint.get("credential_id"))
        secret = (
            CredentialConfigurationApplication(self.workspace).resolve_field(
                credential_id, "api_key"
            )
            if credential_id is not None
            else None
        )
        return model, endpoint, secret

    def _path(self, model_id: str) -> Path:
        return self.workspace.paths.available_models_root() / f"{model_id}.toml"

    def _evidence_path(self, model_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "ai-models", f"{model_id}.json"
        )


@dataclass(frozen=True, slots=True)
class ModelResourceMigrationApplication:
    workspace: Workspace

    def migrate_legacy(self) -> dict[str, object]:
        marker = self.workspace.paths.child(
            "state", "workspace", "layout-migrations", "ai-model-resources-v1.json"
        )
        if marker.is_file():
            return {
                "status": "migrated",
                "created_endpoints": [],
                "created_models": [],
            }
        legacy = ModelProviderConnectionApplication(self.workspace)
        endpoints = ModelEndpointApplication(self.workspace)
        models = AvailableModelApplication(self.workspace)
        created_endpoints: list[str] = []
        created_models: list[str] = []
        for connection in legacy.list():
            endpoint_id = str(connection["connection_id"])
            try:
                existing_endpoint = endpoints.show(endpoint_id)
            except KeyError:
                endpoints.configure(
                    endpoint_id,
                    provider=str(connection["provider"]),
                    api_mode=str(connection["api_mode"]),
                    base_url=str(connection["base_url"]),
                    credential_id=_optional_text(connection.get("credential_id")),
                    timeout_seconds=float(
                        str(connection.get("timeout_seconds") or 60.0)
                    ),
                    enabled=bool(connection.get("enabled", True)),
                )
                created_endpoints.append(endpoint_id)
            else:
                if any(
                    existing_endpoint.get(field) != connection.get(source)
                    for field, source in (
                        ("provider", "provider"),
                        ("api_mode", "api_mode"),
                        ("base_url", "base_url"),
                        ("credential_id", "credential_id"),
                    )
                ):
                    raise ValueError(
                        f"model endpoint migration conflict: {endpoint_id}"
                    )
            for upstream in _sequence(connection.get("models")):
                provider_model = str(upstream)
                model_id = _legacy_model_id(endpoint_id, provider_model)
                try:
                    existing_model = models.show(model_id)
                except KeyError:
                    models.configure(
                        model_id,
                        endpoint_id=endpoint_id,
                        provider_model=provider_model,
                    )
                    created_models.append(model_id)
                else:
                    if (
                        existing_model.get("endpoint_id") != endpoint_id
                        or existing_model.get("provider_model") != provider_model
                    ):
                        raise ValueError(
                            f"available model migration conflict: {model_id}"
                        )
                verification = legacy.verification(endpoint_id, model=provider_model)
                if verification.get("verification_status") in {"verified", "failed"}:
                    models.record_test(
                        model_id,
                        {
                            "succeeded": verification.get("verification_status")
                            == "verified",
                            "detail": verification.get("last_test_detail"),
                            "error_category": verification.get("error_category"),
                        },
                    )
        result = {
            "status": "migrated",
            "created_endpoints": created_endpoints,
            "created_models": created_models,
        }
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, "ai-model-resources-migration-v1"
        )
        transaction.stage_text(
            marker,
            json.dumps(
                {
                    "version": 1,
                    "migration": "ai-model-resources-v1",
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        transaction.commit()
        return result

    def resolve_legacy(
        self, endpoint_id: str, provider_model: str
    ) -> dict[str, object]:
        model_id = _legacy_model_id(endpoint_id, provider_model)
        try:
            return AvailableModelApplication(self.workspace).show(model_id)
        except KeyError:
            connection = ModelProviderConnectionApplication(self.workspace).show(
                endpoint_id
            )
            if provider_model not in map(str, _sequence(connection.get("models"))):
                raise KeyError(
                    f"legacy available model does not exist: {endpoint_id}/{provider_model}"
                )
            return {
                "model_id": model_id,
                "endpoint_id": endpoint_id,
                "provider_model": provider_model,
                "enabled": connection.get("enabled", True),
                "configured": connection.get("configured", False),
                "legacy": True,
                **ModelProviderConnectionApplication(self.workspace).verification(
                    endpoint_id, model=provider_model
                ),
            }


def _endpoint_from_connection(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "endpoint_id": value["connection_id"],
        "provider": value["provider"],
        "provider_label": value.get("provider_label", value["provider"]),
        "api_mode": value["api_mode"],
        "base_url": value["base_url"],
        "credential_id": value.get("credential_id"),
        "timeout_seconds": value.get("timeout_seconds", 60.0),
        "enabled": value.get("enabled", True),
        "configured": value.get("configured", True),
        "issues": list(_sequence(value.get("issues"))),
    }


def _connection_from_endpoint(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "connection_id": value["endpoint_id"],
        "provider": value["provider"],
        "provider_label": value.get("provider_label", value["provider"]),
        "api_mode": value["api_mode"],
        "base_url": value["base_url"],
        "credential_id": value.get("credential_id"),
        "timeout_seconds": value.get("timeout_seconds", 60.0),
        "enabled": value.get("enabled", True),
        "configured": value.get("configured", True),
        "issues": list(_sequence(value.get("issues"))),
    }


def _endpoint_document(value: Mapping[str, object]) -> str:
    lines = [
        "[endpoint]",
        "version = 1",
        f"id = {_toml_string(str(value['endpoint_id']))}",
        f"provider = {_toml_string(str(value['provider']))}",
        f"api_mode = {_toml_string(str(value['api_mode']))}",
        f"base_url = {_toml_string(str(value['base_url']))}",
        f"timeout_seconds = {float(cast(Any, value.get('timeout_seconds') or 60.0))}",
        f"enabled = {'true' if value.get('enabled', True) else 'false'}",
    ]
    if value.get("credential_id") is not None:
        lines.append(f"credential_id = {_toml_string(str(value['credential_id']))}")
    return "\n".join((*lines, ""))


def _available_model_document(value: Mapping[str, object]) -> str:
    return "\n".join(
        (
            "[model]",
            "version = 1",
            f"id = {_toml_string(str(value['model_id']))}",
            f"endpoint = {_toml_string(str(value['endpoint_id']))}",
            f"provider_model = {_toml_string(str(value['provider_model']))}",
            f"enabled = {'true' if value.get('enabled', True) else 'false'}",
            "",
        )
    )


def _pending(configuration_hash: str | None, model_id: str) -> dict[str, object]:
    return {
        "verification_status": "pending",
        "model_id": model_id,
        "last_tested_at": None,
        "tested_configuration_hash": None,
        "current_configuration_hash": configuration_hash,
        "tested": [],
        "not_tested": [],
        "capabilities": [],
    }


def _provider_label(
    application: ModelProviderConnectionApplication, provider: str
) -> str:
    return next(
        (
            str(value.get("label") or provider)
            for value in application.provider_catalog()
            if value.get("provider") == provider
        ),
        provider,
    )


def _legacy_model_id(endpoint_id: str, provider_model: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", provider_model).strip("-").lower()
    normalized = normalized[:72] or "model"
    digest = hashlib.sha256(provider_model.encode("utf-8")).hexdigest()[:8]
    return _safe_model_id(f"{endpoint_id}-{normalized}-{digest}")


def _provider_model(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 256 or any(character.isspace() for character in value):
        raise ValueError("provider_model must be non-empty and contain no whitespace")
    return value


def _safe_model_id(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9_-])?", value):
        raise ValueError("model_id must be a path-safe identifier")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_id(value: str, name: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError(f"{name} must be a path-safe identifier")
    return value


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _hash(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "AvailableModelApplication",
    "ModelEndpointApplication",
    "ModelResourceMigrationApplication",
    "PreparedAvailableModel",
    "PreparedModelEndpoint",
]
