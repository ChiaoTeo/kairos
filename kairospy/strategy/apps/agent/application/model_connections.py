"""Workspace model-provider connections and isolated verification evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import (
    Workspace,
    WorkspaceConfigurationTransaction,
)


API_MODES = (
    "openai-responses",
    "openai-chat-completions",
    "anthropic-messages",
    "ollama-native",
)

_PROVIDERS: Mapping[str, Mapping[str, object]] = {
    "openai": {
        "label": "OpenAI",
        "api_mode": "openai-responses",
        "base_url": "https://api.openai.com/v1",
        "credential_provider": "openai",
        "auth_required": True,
        "group": "hosted",
    },
    "anthropic": {
        "label": "Anthropic",
        "api_mode": "anthropic-messages",
        "base_url": "https://api.anthropic.com/v1",
        "credential_provider": "anthropic",
        "auth_required": True,
        "group": "hosted",
    },
    "openrouter": {
        "label": "OpenRouter",
        "api_mode": "openai-chat-completions",
        "base_url": "https://openrouter.ai/api/v1",
        "credential_provider": "openrouter",
        "auth_required": True,
        "group": "hosted",
    },
    "ollama": {
        "label": "Ollama",
        "api_mode": "openai-chat-completions",
        "base_url": "http://127.0.0.1:11434/v1",
        "credential_provider": "ollama",
        "auth_required": False,
        "group": "local",
    },
    "lmstudio": {
        "label": "LM Studio",
        "api_mode": "openai-chat-completions",
        "base_url": "http://127.0.0.1:1234/v1",
        "credential_provider": "lmstudio",
        "auth_required": False,
        "group": "local",
    },
}


ModelProbe = Callable[[Mapping[str, object], str | None, str], object]
CatalogProbe = Callable[
    [Mapping[str, object], str | None], Sequence[Mapping[str, object]]
]


@dataclass(frozen=True, slots=True)
class PreparedModelConnection:
    workspace: Workspace
    connection: Mapping[str, object]
    document: str

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        transaction.stage_text(
            self.workspace.paths.model_connections_root()
            / f"{self.connection['connection_id']}.toml",
            self.document,
        )


@dataclass(frozen=True, slots=True)
class ModelProviderConnectionApplication:
    workspace: Workspace

    def provider_catalog(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {"provider": provider, **dict(value)}
            for provider, value in _PROVIDERS.items()
        )

    def provider_defaults(self, provider: str) -> dict[str, object]:
        value = _PROVIDERS.get(provider.strip().lower())
        if value is None:
            raise ValueError(f"unsupported model provider: {provider}")
        return {"provider": provider.strip().lower(), **dict(value)}

    def configure(
        self,
        connection_id: str,
        *,
        provider: str,
        api_mode: str | None = None,
        base_url: str | None = None,
        credential_id: str | None = None,
        models: Sequence[str] = (),
        timeout_seconds: float = 60.0,
        enabled: bool = True,
        overwrite: bool = False,
    ) -> dict[str, object]:
        connection_id = _safe_id(connection_id, "connection_id")
        path = self._path(connection_id)
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        prepared = self.prepare(
            connection_id,
            provider=provider,
            api_mode=api_mode,
            base_url=base_url,
            credential_id=credential_id,
            models=models,
            timeout_seconds=timeout_seconds,
            enabled=enabled,
        )
        _write_private_atomic(path, prepared.document, overwrite=overwrite)
        return self.show(connection_id)

    def prepare(
        self,
        connection_id: str,
        *,
        provider: str,
        api_mode: str | None = None,
        base_url: str | None = None,
        credential_id: str | None = None,
        credential_provider: str | None = None,
        models: Sequence[str] = (),
        timeout_seconds: float = 60.0,
        enabled: bool = True,
    ) -> PreparedModelConnection:
        connection_id = _safe_id(connection_id, "connection_id")
        provider = provider.strip().lower()
        defaults = dict(_PROVIDERS.get(provider, {}))
        custom = provider not in _PROVIDERS
        selected_mode = str(api_mode or defaults.get("api_mode") or "").strip()
        if selected_mode not in API_MODES:
            raise ValueError(
                "model api_mode must be openai-responses, "
                "openai-chat-completions, anthropic-messages, or ollama-native"
            )
        endpoint = str(base_url or defaults.get("base_url") or "").strip().rstrip("/")
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError("model provider base_url must use HTTP or HTTPS")
        if timeout_seconds <= 0 or timeout_seconds > 600:
            raise ValueError("model provider timeout_seconds must be between 0 and 600")
        normalized_models = tuple(
            dict.fromkeys(_model_id(value) for value in models if value.strip())
        )
        if credential_id is not None:
            credential_id = _safe_id(credential_id, "credential_id")
            expected = str(defaults.get("credential_provider") or "custom-model")
            actual_provider = credential_provider
            if actual_provider is None:
                credential = CredentialConfigurationApplication(self.workspace).show(
                    credential_id
                )
                actual_provider = str(credential.get("provider") or "")
            if actual_provider != expected:
                raise ValueError(
                    f"model provider {provider} requires a {expected} credential"
                )
        elif defaults.get("auth_required", custom) is True:
            raise ValueError(f"model provider {provider} requires a credential")

        connection = {
            "connection_id": connection_id,
            "provider": provider,
            "provider_label": defaults.get("label", provider),
            "api_mode": selected_mode,
            "base_url": endpoint,
            "credential_id": credential_id,
            "models": list(normalized_models),
            "timeout_seconds": float(timeout_seconds),
            "enabled": enabled,
            "configured": enabled,
            "issues": [],
        }
        return PreparedModelConnection(
            self.workspace,
            connection,
            _model_connection_document(connection),
        )

    def set_enabled(self, connection_id: str, *, enabled: bool) -> dict[str, object]:
        current = self.show(connection_id)
        return self.configure(
            connection_id,
            provider=str(current["provider"]),
            api_mode=str(current["api_mode"]),
            base_url=str(current["base_url"]),
            credential_id=(
                str(current["credential_id"])
                if current.get("credential_id") is not None
                else None
            ),
            models=tuple(
                str(value) for value in cast(Any, current.get("models") or ())
            ),
            timeout_seconds=float(cast(Any, current.get("timeout_seconds") or 60.0)),
            enabled=enabled,
            overwrite=True,
        )

    def list(self) -> tuple[dict[str, object], ...]:
        root = self.workspace.paths.model_connections_root()
        if not root.is_dir():
            return ()
        values: list[dict[str, object]] = []
        for path in sorted(root.glob("*.toml")):
            try:
                values.append(self._summary(path))
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                values.append(
                    {
                        "connection_id": path.stem,
                        "configured": False,
                        "issues": ["模型连接配置无效"],
                        "verification_status": "pending",
                    }
                )
        return tuple(values)

    def show(self, connection_id: str) -> dict[str, object]:
        connection_id = _safe_id(connection_id, "connection_id")
        path = self._path(connection_id)
        if not path.is_file():
            raise KeyError(f"model connection does not exist: {connection_id}")
        return self._summary(path)

    def delete(self, connection_id: str) -> dict[str, str]:
        connection_id = _safe_id(connection_id, "connection_id")
        path = self._path(connection_id)
        if not path.is_file():
            raise KeyError(f"model connection does not exist: {connection_id}")
        path.unlink()
        self._evidence_path(connection_id).unlink(missing_ok=True)
        return {"connection_id": connection_id, "status": "deleted"}

    def discover_models(
        self,
        connection_id: str,
        *,
        probe: CatalogProbe | None = None,
    ) -> tuple[dict[str, object], ...]:
        connection = self.show(connection_id)
        secret = self._secret(connection)
        return self.discover(connection, secret=secret, probe=probe)

    def discover(
        self,
        connection: Mapping[str, object],
        *,
        secret: str | None,
        probe: CatalogProbe | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Discover models for a saved or staged connection without persisting."""

        values = (probe or _discover_models)(connection, secret)
        result: list[dict[str, object]] = []
        for value in values:
            model_id = _model_id(str(value.get("id") or ""))
            result.append(
                {
                    "id": model_id,
                    "name": str(value.get("name") or model_id),
                    "input": list(cast(Any, value.get("input") or ("text",))),
                    "tool_calling": value.get("tool_calling"),
                    "reasoning": value.get("reasoning"),
                    "source": str(value.get("source") or "provider-catalog"),
                }
            )
        return tuple(result)

    def detect_local(
        self,
        *,
        probe: Callable[[str, str], Sequence[Mapping[str, object]]] | None = None,
    ) -> tuple[dict[str, object], ...]:
        detected: list[dict[str, object]] = []
        for provider in ("ollama", "lmstudio"):
            defaults = self.provider_defaults(provider)
            try:
                models = tuple(
                    (probe or _detect_local_models)(provider, str(defaults["base_url"]))
                )
            except (OSError, ValueError, HTTPError, URLError, TimeoutError):
                continue
            detected.append(
                {
                    "provider": provider,
                    "label": defaults["label"],
                    "base_url": defaults["base_url"],
                    "models": [str(value.get("id") or "") for value in models],
                    "model_count": len(models),
                }
            )
        return tuple(detected)

    def test(
        self,
        connection_id: str,
        model: str,
        *,
        probe: ModelProbe | None = None,
    ) -> dict[str, object]:
        connection = self.show(connection_id)
        if connection.get("configured") is not True:
            raise ValueError(f"model connection is not ready: {connection_id}")
        model = _model_id(model)
        secret = self._secret(connection)
        result = self.probe(connection, model, secret=secret, probe=probe)
        return self.record_probe(connection_id, model, result)

    def probe(
        self,
        connection: Mapping[str, object],
        model: str,
        *,
        secret: str | None,
        probe: ModelProbe | None = None,
    ) -> dict[str, object]:
        """Test a staged connection without reading or writing Workspace state."""

        model = _model_id(model)
        try:
            (probe or _probe_model)(connection, secret, model)
        except Exception as error:
            return {
                "succeeded": False,
                "detail": "最小文本响应失败",
                "error_category": _probe_error_category(error),
            }
        return {
            "succeeded": True,
            "detail": "最小文本响应成功",
            "error_category": None,
        }

    def record_probe(
        self,
        connection_id: str,
        model: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        """Attach a staged probe result after the matching config is committed."""

        connection = self.show(connection_id)
        model = _model_id(model)
        succeeded = result.get("succeeded") is True
        evidence = {
            "version": 2,
            "connection_id": connection_id,
            "provider": connection.get("provider"),
            "api_mode": connection.get("api_mode"),
            "model": model,
            "model_ref": f"{connection_id}/{model}",
            "configuration_hash": self._configuration_hash(connection),
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
        _write_private_atomic(
            self._evidence_path(connection_id),
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            overwrite=True,
        )
        return self.verification(connection_id, model=model)

    def verification(
        self, connection_id: str, *, model: str | None = None
    ) -> dict[str, object]:
        path = self._path(_safe_id(connection_id, "connection_id"))
        if not path.is_file():
            return _pending_verification(None)
        connection = self._base_summary(path)
        return self._verification_for(connection, model=model)

    def _verification_for(
        self, connection: Mapping[str, object], *, model: str | None = None
    ) -> dict[str, object]:
        connection_id = str(connection["connection_id"])
        try:
            evidence = json.loads(
                self._evidence_path(connection_id).read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return _pending_verification(self._configuration_hash(connection))
        if not isinstance(evidence, Mapping):
            return _pending_verification(self._configuration_hash(connection))
        changed = evidence.get("configuration_hash") != self._configuration_hash(
            connection
        )
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
            "model_ref": evidence.get("model_ref"),
            "last_tested_at": evidence.get("tested_at"),
            "last_test_detail": evidence.get("detail"),
            "error_category": evidence.get("error_category"),
            "tested_configuration_hash": evidence.get("configuration_hash"),
            "current_configuration_hash": self._configuration_hash(connection),
            "tested": list(evidence.get("tested") or ()),
            "not_tested": list(evidence.get("not_tested") or ()),
            "capabilities": list(evidence.get("capabilities") or ()),
        }

    def resource_snapshot(self, connection_id: str, *, model: str) -> dict[str, object]:
        connection = self.show(connection_id)
        return {
            "connection_id": connection_id,
            "provider": connection.get("provider"),
            "api_mode": connection.get("api_mode"),
            "base_url": connection.get("base_url"),
            "credential_id": connection.get("credential_id"),
            "model": model,
            "model_ref": f"{connection_id}/{model}",
            "verification": self.verification(connection_id, model=model),
            "resource_hash": self._configuration_hash(connection),
        }

    def _summary(self, path: Path) -> dict[str, object]:
        result = self._base_summary(path)
        verification = self._verification_for(result)
        if result.get("enabled") is False:
            verification = {**verification, "verification_status": "disabled"}
        return {**result, **verification}

    def _base_summary(self, path: Path) -> dict[str, object]:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        raw = value.get("connection", value)
        if not isinstance(raw, Mapping):
            raise ValueError("model connection root must be a table")
        connection_id = _safe_id(str(raw.get("id") or path.stem), "connection_id")
        provider = str(raw.get("provider") or "").strip().lower()
        api_mode = str(raw.get("api_mode") or "").strip()
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        credential_id = raw.get("credential_id")
        enabled = bool(raw.get("enabled", True))
        issues: list[str] = []
        credential: Mapping[str, object] | None = None
        if api_mode not in API_MODES:
            issues.append("接口模式不受支持")
        if not base_url.startswith(("https://", "http://")):
            issues.append("Endpoint 无效")
        if isinstance(credential_id, str) and credential_id:
            try:
                credential = CredentialConfigurationApplication(self.workspace).show(
                    credential_id
                )
            except (KeyError, OSError, ValueError):
                issues.append("安全凭据不存在")
            if credential is not None and credential.get("configured") is not True:
                issues.extend(
                    str(item)
                    for item in cast(Any, credential.get("issues") or ())
                )
        elif _PROVIDERS.get(provider, {}).get("auth_required", True) is True:
            issues.append("缺少安全凭据")
        return {
            "connection_id": connection_id,
            "provider": provider,
            "provider_label": _PROVIDERS.get(provider, {}).get("label", provider),
            "api_mode": api_mode,
            "base_url": base_url,
            "credential_id": credential_id if isinstance(credential_id, str) else None,
            "models": list(raw.get("models") or ()),
            "timeout_seconds": float(raw.get("timeout_seconds") or 60.0),
            "enabled": enabled,
            "configured": enabled and not issues,
            "issues": issues,
        }

    def _secret(self, connection: Mapping[str, object]) -> str | None:
        credential_id = connection.get("credential_id")
        if not isinstance(credential_id, str) or not credential_id:
            return None
        return CredentialConfigurationApplication(self.workspace).resolve_field(
            credential_id, "api_key"
        )

    def _configuration_hash(self, connection: Mapping[str, object]) -> str:
        credential_id = connection.get("credential_id")
        credential_hash: str | None = None
        if isinstance(credential_id, str) and credential_id:
            try:
                credential_hash = str(
                    CredentialConfigurationApplication(
                        self.workspace
                    ).resource_snapshot(credential_id)["resource_hash"]
                )
            except (KeyError, OSError, ValueError):
                credential_hash = "unavailable"
        payload: dict[str, object] = {
            "connection_id": connection.get("connection_id"),
            "provider": connection.get("provider"),
            "api_mode": connection.get("api_mode"),
            "base_url": connection.get("base_url"),
            "credential_id": credential_id,
            "credential_hash": credential_hash,
            "models": list(cast(Any, connection.get("models") or ())),
            "timeout_seconds": connection.get("timeout_seconds"),
        }
        if connection.get("enabled", True) is False:
            payload["enabled"] = False
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _path(self, connection_id: str) -> Path:
        return self.workspace.paths.model_connections_root() / f"{connection_id}.toml"

    def _evidence_path(self, connection_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "models", f"{connection_id}.json"
        )


def _pending_verification(configuration_hash: str | None) -> dict[str, object]:
    return {
        "verification_status": "pending",
        "last_tested_at": None,
        "tested_configuration_hash": None,
        "current_configuration_hash": configuration_hash,
        "tested": [],
        "not_tested": [],
        "capabilities": [],
    }


def _discover_models(
    connection: Mapping[str, object], secret: str | None
) -> Sequence[Mapping[str, object]]:
    mode = str(connection["api_mode"])
    base_url = str(connection["base_url"])
    timeout = float(cast(Any, connection.get("timeout_seconds") or 60.0))
    if mode == "ollama-native":
        payload = _request_json(f"{base_url}/api/tags", timeout=timeout)
        values = payload.get("models", []) if isinstance(payload, Mapping) else []
        return [
            {"id": str(value.get("name") or value.get("model") or "")}
            for value in values
            if isinstance(value, Mapping)
        ]
    headers = _auth_headers(mode, secret)
    payload = _request_json(f"{base_url}/models", headers=headers, timeout=timeout)
    values = payload.get("data", []) if isinstance(payload, Mapping) else []
    return [
        {"id": str(value.get("id") or "")}
        for value in values
        if isinstance(value, Mapping)
    ]


def _detect_local_models(
    provider: str, base_url: str
) -> Sequence[Mapping[str, object]]:
    native_base_url = base_url.removesuffix("/v1") if provider == "ollama" else base_url
    connection = {
        "api_mode": "ollama-native"
        if provider == "ollama"
        else "openai-chat-completions",
        "base_url": native_base_url,
        "timeout_seconds": 0.35,
    }
    return _discover_models(connection, None)


def _probe_model(
    connection: Mapping[str, object], secret: str | None, model: str
) -> object:
    mode = str(connection["api_mode"])
    base_url = str(connection["base_url"])
    timeout = float(cast(Any, connection.get("timeout_seconds") or 60.0))
    if mode == "openai-responses":
        return _request_json(
            f"{base_url}/responses",
            headers=_auth_headers(mode, secret),
            payload={
                "model": model,
                "input": "Reply with OK to verify this Kairos model connection.",
                "max_output_tokens": 8,
                "store": False,
            },
            timeout=timeout,
        )
    if mode == "openai-chat-completions":
        return _request_json(
            f"{base_url}/chat/completions",
            headers=_auth_headers(mode, secret),
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 8,
                "stream": False,
            },
            timeout=timeout,
        )
    if mode == "anthropic-messages":
        return _request_json(
            f"{base_url}/messages",
            headers=_auth_headers(mode, secret),
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 8,
            },
            timeout=timeout,
        )
    return _request_json(
        f"{base_url}/api/chat",
        payload={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
        },
        timeout=timeout,
    )


def _auth_headers(mode: str, secret: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if secret:
        if mode == "anthropic-messages":
            headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
        else:
            headers["Authorization"] = f"Bearer {secret}"
    return headers


def _request_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    payload: Mapping[str, object] | None = None,
    timeout: float,
) -> object:
    request = Request(
        url,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers=dict(headers or {}),
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _probe_error_category(error: Exception) -> str:
    if isinstance(error, HTTPError):
        if error.code in {401, 403}:
            return "authentication_or_permission"
        if error.code == 404:
            return "model_or_endpoint_not_found"
        if error.code == 429:
            return "rate_limit_or_quota"
        return "provider_http_error"
    if isinstance(error, (URLError, TimeoutError, OSError)):
        return "network_unavailable"
    if isinstance(error, (json.JSONDecodeError, TypeError, ValueError)):
        return "protocol_or_response_invalid"
    return "provider_error"


def _safe_id(value: str, name: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError(f"{name} must be a path-safe identifier")
    return value


def _model_id(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 256 or any(character.isspace() for character in value):
        raise ValueError("model id must be a non-empty value without whitespace")
    return value


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _model_connection_document(connection: Mapping[str, object]) -> str:
    models = tuple(
        str(value) for value in cast(Any, connection.get("models") or ())
    )
    lines = [
        "[connection]",
        "version = 1",
        f"id = {_toml_string(str(connection['connection_id']))}",
        f"provider = {_toml_string(str(connection['provider']))}",
        f"api_mode = {_toml_string(str(connection['api_mode']))}",
        f"base_url = {_toml_string(str(connection['base_url']))}",
        f"timeout_seconds = {float(cast(Any, connection.get('timeout_seconds') or 60.0))}",
        f"enabled = {'true' if connection.get('enabled', True) else 'false'}",
        "models = [" + ", ".join(_toml_string(value) for value in models) + "]",
    ]
    credential_id = connection.get("credential_id")
    if credential_id is not None:
        lines.append(f"credential_id = {_toml_string(str(credential_id))}")
    return "\n".join((*lines, ""))


def _write_private_atomic(path: Path, content: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
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


__all__ = ["API_MODES", "ModelProviderConnectionApplication"]
