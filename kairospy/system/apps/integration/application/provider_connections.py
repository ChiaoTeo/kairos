"""Integration-owned provider connection configuration and verification."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any
from urllib.request import Request, urlopen

from kairospy.system.apps.configuration.services.transactions import (
    WorkspaceConfigurationTransaction,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.domain.workspace import Workspace


ConnectionProbe = Callable[
    [Mapping[str, object], Mapping[str, str]], Mapping[str, object]
]

_PROVIDERS: Mapping[str, Mapping[str, object]] = {
    "massive": {
        "default_endpoint": "https://api.massive.com",
        "products": ("equity", "options"),
        "purposes": ("reference-catalog", "market-query", "market-stream"),
        "credential_fields": ("api_key",),
    },
    "binance": {
        "default_endpoint": "https://api.binance.com",
        "products": ("spot", "equity", "usd-m-futures", "coin-m-futures"),
        "purposes": ("market-query", "market-stream", "account-read", "order-trade"),
        "credential_fields": ("api_key", "api_secret"),
    },
    "okx": {
        "default_endpoint": "https://www.okx.com",
        "products": ("spot", "swap", "futures", "options"),
        "purposes": ("market-query", "market-stream", "account-read", "order-trade"),
        "credential_fields": ("api_key", "api_secret", "passphrase"),
    },
}
_ENVIRONMENTS = frozenset({"production", "testnet"})


@dataclass(frozen=True, slots=True)
class PreparedProviderConnection:
    workspace: Workspace
    connection: Mapping[str, object]
    document: str

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        connection_id = str(self.connection["connection_id"])
        transaction.stage_text(
            self.workspace.paths.provider_connections_root() / f"{connection_id}.toml",
            self.document,
        )


@dataclass(frozen=True, slots=True)
class ProviderConnectionConfigurationApplication:
    """Configure provider access without owning the consuming business facts."""

    workspace: Workspace

    def catalog(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "provider": provider,
                "default_endpoint": value["default_endpoint"],
                "products": list(_sequence(value["products"])),
                "purposes": list(_sequence(value["purposes"])),
                "credential_fields": list(_sequence(value["credential_fields"])),
            }
            for provider, value in _PROVIDERS.items()
        )

    def configure(
        self,
        connection_id: str,
        *,
        provider: str,
        credential_id: str,
        products: Sequence[str],
        purposes: Sequence[str],
        endpoint: str | None = None,
        endpoints: Mapping[str, str] | None = None,
        environment: str = "production",
        enabled: bool = True,
        overwrite: bool = False,
    ) -> dict[str, object]:
        prepared = self.prepare(
            connection_id,
            provider=provider,
            credential_id=credential_id,
            products=products,
            purposes=purposes,
            endpoint=endpoint,
            endpoints=endpoints,
            environment=environment,
            enabled=enabled,
        )
        path = self._path(str(prepared.connection["connection_id"]))
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        transaction = WorkspaceConfigurationTransaction(
            self.workspace,
            f"provider-connection:{prepared.connection['connection_id']}",
        )
        prepared.stage(transaction)
        transaction.commit()
        return self.show(str(prepared.connection["connection_id"]))

    def prepare(
        self,
        connection_id: str,
        *,
        provider: str,
        credential_id: str,
        products: Sequence[str],
        purposes: Sequence[str],
        endpoint: str | None = None,
        endpoints: Mapping[str, str] | None = None,
        environment: str = "production",
        enabled: bool = True,
        credential_provider: str | None = None,
        credential_fields: Sequence[str] | None = None,
    ) -> PreparedProviderConnection:
        connection_id = _safe_id(connection_id, "connection_id")
        provider = provider.strip().lower()
        definition = _PROVIDERS.get(provider)
        if definition is None:
            raise ValueError(f"unsupported provider: {provider}")
        environment = environment.strip().lower()
        if environment not in _ENVIRONMENTS:
            raise ValueError("environment must be production or testnet")
        endpoint = (endpoint or str(definition["default_endpoint"])).strip().rstrip("/")
        if not endpoint.startswith("https://"):
            raise ValueError("provider endpoint must use HTTPS")
        selected_endpoints = _endpoints(endpoints)
        selected_products = _selection(products, "products")
        selected_purposes = _selection(purposes, "purposes")
        _require_supported(
            selected_products, definition["products"], "products", provider
        )
        _require_supported(
            selected_purposes, definition["purposes"], "purposes", provider
        )
        credential_id = _safe_id(credential_id, "credential_id")
        credential: Mapping[str, object]
        if credential_provider is None:
            credential = CredentialConfigurationApplication(
                self.workspace
            ).resource_snapshot(credential_id)
            credential_provider = str(credential.get("provider") or "")
            credential_fields = [
                str(value) for value in _sequence(credential.get("fields"))
            ]
        else:
            credential = {
                "provider": credential_provider,
                "fields": list(credential_fields or ()),
            }
        if credential_provider != provider:
            raise ValueError(f"{provider} connection requires a {provider} credential")
        fields = set(str(value) for value in credential_fields or ())
        required_fields = {
            str(value) for value in _sequence(definition["credential_fields"])
        }
        missing = sorted(required_fields - fields)
        if missing:
            raise ValueError(
                f"{provider} credential is missing required values: {', '.join(missing)}"
            )
        connection = {
            "connection_id": connection_id,
            "provider": provider,
            "environment": environment,
            "endpoint": endpoint,
            "endpoints": selected_endpoints,
            "credential_id": credential_id,
            "enabled": bool(enabled),
            "products": list(selected_products),
            "purposes": list(selected_purposes),
        }
        return PreparedProviderConnection(
            self.workspace, connection, _connection_document(connection)
        )

    def list(self) -> list[dict[str, object]]:
        root = self.workspace.paths.provider_connections_root()
        if not root.is_dir():
            return []
        records: list[dict[str, object]] = []
        for path in sorted(root.glob("*.toml")):
            try:
                records.append(self.show(path.stem))
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                records.append(
                    {
                        "connection_id": path.stem,
                        "provider": "unknown",
                        "configured": False,
                        "issues": ["provider connection configuration is invalid"],
                        "verification_status": "pending",
                    }
                )
        return records

    def show(self, connection_id: str) -> dict[str, object]:
        connection_id = _safe_id(connection_id, "connection_id")
        path = self._path(connection_id)
        if not path.is_file():
            raise KeyError(f"provider connection does not exist: {connection_id}")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        value = raw.get("connection", raw)
        if not isinstance(value, Mapping):
            raise ValueError(
                "provider connection document must contain a connection table"
            )
        connection = self._normalize(connection_id, value)
        return {
            **connection,
            **self.verification(connection_id, configuration=connection),
        }

    def set_enabled(self, connection_id: str, *, enabled: bool) -> dict[str, object]:
        current = self.show(connection_id)
        return self.configure(
            connection_id,
            provider=str(current["provider"]),
            credential_id=str(current["credential_id"]),
            products=[str(value) for value in _sequence(current["products"])],
            purposes=[str(value) for value in _sequence(current["purposes"])],
            endpoint=str(current["endpoint"]),
            endpoints=_string_mapping(current.get("endpoints")),
            environment=str(current["environment"]),
            enabled=enabled,
            overwrite=True,
        )

    def delete(
        self, connection_id: str, *, referenced_by: Sequence[str] = ()
    ) -> dict[str, str]:
        connection_id = _safe_id(connection_id, "connection_id")
        references = sorted(
            set(value.strip() for value in referenced_by if value.strip())
        )
        if not references:
            from kairospy.system.apps.configuration.application.references import (
                ConfigurationReferenceApplication,
            )

            references = sorted(
                f"{value.get('source')}:{value.get('location')}"
                for value in ConfigurationReferenceApplication(
                    self.workspace
                ).data_provider_references(connection_id)
            )
        if references:
            raise ValueError(
                f"provider connection is still referenced by: {', '.join(references)}"
            )
        path = self._path(connection_id)
        if not path.is_file():
            raise KeyError(f"provider connection does not exist: {connection_id}")
        path.unlink()
        self._evidence_path(connection_id).unlink(missing_ok=True)
        return {"connection_id": connection_id, "status": "deleted"}

    def test_connection(
        self, connection_id: str, *, probe: ConnectionProbe | None = None
    ) -> dict[str, object]:
        connection = self.show(connection_id)
        if not connection.get("configured"):
            raise ValueError(
                "provider connection must pass static validation before testing"
            )
        credential_id = str(connection["credential_id"])
        definition = _PROVIDERS[str(connection["provider"])]
        credentials = CredentialConfigurationApplication(self.workspace)
        secrets = {
            str(field): value
            for field in _sequence(definition["credential_fields"])
            if (value := credentials.resolve_field(credential_id, str(field)))
            is not None
        }
        tested_at = datetime.now(timezone.utc).isoformat()
        try:
            observed = dict((probe or _probe_market_connection)(connection, secrets))
            capabilities = sorted(
                set(str(value) for value in _sequence(observed.get("capabilities")))
            )
            evidence = {
                "schema_version": 1,
                "connection_id": connection_id,
                "configuration_hash": self._fingerprint(connection),
                "result": "verified",
                "tested_at": tested_at,
                "capabilities": capabilities,
                "observed_permissions": sorted(
                    set(
                        str(value)
                        for value in _sequence(observed.get("observed_permissions"))
                    )
                ),
                "warnings": list(_sequence(observed.get("warnings"))),
            }
        except Exception as error:
            evidence = {
                "schema_version": 1,
                "connection_id": connection_id,
                "configuration_hash": self._fingerprint(connection),
                "result": "failed",
                "tested_at": tested_at,
                "capabilities": [],
                "observed_permissions": [],
                "warnings": [],
                "error_category": _error_category(error),
            }
        path = self._evidence_path(connection_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"provider-connection-evidence:{connection_id}"
        )
        transaction.stage_text(
            path, json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        )
        transaction.commit()
        return self.verification(connection_id)

    def record_probe_result(
        self,
        connection_id: str,
        *,
        succeeded: bool,
        capabilities: Sequence[str] = (),
        observed_permissions: Sequence[str] = (),
        warnings: Sequence[str] = (),
        error_category: str | None = None,
    ) -> dict[str, object]:
        """Record normalized evidence produced by a provider-specific probe."""

        connection = self.show(connection_id)
        evidence = {
            "schema_version": 1,
            "connection_id": connection_id,
            "configuration_hash": self._fingerprint(connection),
            "result": "verified" if succeeded else "failed",
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "capabilities": sorted(set(capabilities)) if succeeded else [],
            "observed_permissions": sorted(set(observed_permissions)),
            "warnings": list(warnings),
            "error_category": None
            if succeeded
            else error_category or "provider_response",
        }
        path = self._evidence_path(connection_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"provider-connection-evidence:{connection_id}"
        )
        transaction.stage_text(
            path, json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        )
        transaction.commit()
        return self.verification(connection_id)

    def verification(
        self,
        connection_id: str,
        *,
        configuration: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        current = configuration or self._base_configuration(connection_id)
        current_hash = self._fingerprint(current)
        path = self._evidence_path(connection_id)
        if not path.is_file():
            return _pending_verification(current_hash)
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _pending_verification(current_hash)
        status = str(evidence.get("result") or "failed")
        if evidence.get("configuration_hash") != current_hash:
            status = "retest_required"
        return {
            "verification_status": status,
            "last_tested_at": evidence.get("tested_at"),
            "capabilities_verified": list(evidence.get("capabilities") or ()),
            "observed_permissions": list(evidence.get("observed_permissions") or ()),
            "warnings": list(evidence.get("warnings") or ()),
            "error_category": evidence.get("error_category"),
            "tested_configuration_hash": evidence.get("configuration_hash"),
            "current_configuration_hash": current_hash,
        }

    def resource_snapshot(self, connection_id: str) -> dict[str, object]:
        connection = self.show(connection_id)
        credential = CredentialConfigurationApplication(
            self.workspace
        ).resource_snapshot(str(connection["credential_id"]))
        return {
            **connection,
            "credential_identity": {
                "credential_id": credential.get("credential_id"),
                "provider": credential.get("provider"),
                "role": credential.get("role"),
                "fields": credential.get("fields", []),
            },
            "resource_hash": self._fingerprint(connection),
        }

    def _normalize(
        self, connection_id: str, value: Mapping[str, object]
    ) -> dict[str, object]:
        provider = str(value.get("provider") or value.get("type") or "").lower()
        definition = _PROVIDERS.get(provider)
        issues: list[str] = []
        if definition is None:
            issues.append(f"unsupported provider: {provider or 'missing'}")
            definition = {"products": (), "purposes": (), "credential_fields": ()}
        credential_id = str(value.get("credential_id") or "")
        try:
            credential = CredentialConfigurationApplication(
                self.workspace
            ).resource_snapshot(credential_id)
        except (KeyError, OSError, ValueError):
            credential = {}
            issues.append(f"credential does not exist: {credential_id or 'missing'}")
        if credential and credential.get("provider") != provider:
            issues.append("credential provider does not match connection provider")
        products = [str(item) for item in _sequence(value.get("products"))]
        purposes = [str(item) for item in _sequence(value.get("purposes"))]
        unsupported_products = sorted(
            set(products) - set(_sequence(definition["products"]))
        )
        unsupported_purposes = sorted(
            set(purposes) - set(_sequence(definition["purposes"]))
        )
        if unsupported_products:
            issues.append(f"unsupported products: {', '.join(unsupported_products)}")
        if unsupported_purposes:
            issues.append(f"unsupported purposes: {', '.join(unsupported_purposes)}")
        endpoint = str(value.get("endpoint") or "")
        if not endpoint.startswith("https://"):
            issues.append("provider endpoint must use HTTPS")
        try:
            endpoints = _endpoints(_string_mapping(value.get("endpoints")))
        except ValueError as error:
            endpoints = {}
            issues.append(str(error))
        environment = str(value.get("environment") or "production").lower()
        if environment not in _ENVIRONMENTS:
            issues.append("environment must be production or testnet")
        return {
            "connection_id": connection_id,
            "provider": provider,
            "environment": environment,
            "endpoint": endpoint,
            "endpoints": endpoints,
            "credential_id": credential_id,
            "enabled": bool(value.get("enabled", True)),
            "products": products,
            "purposes": purposes,
            "configured": bool(value.get("enabled", True)) and not issues,
            "issues": issues,
        }

    def _base_configuration(self, connection_id: str) -> Mapping[str, object]:
        value = self.show(connection_id)
        return {key: value[key] for key in _FINGERPRINT_FIELDS}

    def _fingerprint(self, value: Mapping[str, object]) -> str:
        credential_id = str(value.get("credential_id") or "")
        try:
            credential_hash = (
                CredentialConfigurationApplication(self.workspace)
                .resource_snapshot(credential_id)
                .get("resource_hash")
            )
        except (KeyError, OSError, ValueError):
            credential_hash = None
        payload = {
            key: sorted(str(item) for item in _sequence(value.get(key)))
            if key in {"products", "purposes"}
            else value.get(key)
            for key in _FINGERPRINT_FIELDS
        }
        payload["credential_resource_hash"] = credential_hash
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _path(self, connection_id: str) -> Path:
        return self.workspace.paths.provider_connections_root() / f"{connection_id}.toml"

    def _evidence_path(self, connection_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "provider-connections", f"{connection_id}.json"
        )


_FINGERPRINT_FIELDS = (
    "connection_id",
    "provider",
    "environment",
    "endpoint",
    "endpoints",
    "credential_id",
    "enabled",
    "products",
    "purposes",
)


def _safe_id(value: str, name: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError(f"{name} must be a path-safe lowercase identifier")
    return value


def _selection(values: Sequence[str], name: str) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(str(value).strip().lower() for value in values))
    if not selected or any(not value for value in selected):
        raise ValueError(f"{name} must contain at least one value")
    return selected


def _require_supported(
    selected: Sequence[str], supported: object, name: str, provider: str
) -> None:
    allowed = (
        set(str(value) for value in supported)
        if isinstance(supported, Sequence)
        else set()
    )
    unknown = sorted(set(selected) - allowed)
    if unknown:
        raise ValueError(f"unsupported {provider} {name}: {', '.join(unknown)}")


def _connection_document(connection: Mapping[str, object]) -> str:
    lines = ["version = 2", "", "[connection]"]
    for key in (
        "connection_id",
        "provider",
        "environment",
        "endpoint",
        "credential_id",
        "enabled",
        "products",
        "purposes",
    ):
        lines.append(f"{key} = {_toml_value(connection[key])}")
    endpoints = _string_mapping(connection.get("endpoints"))
    if endpoints:
        lines.extend(("", "[connection.endpoints]"))
        lines.extend(
            f"{json.dumps(key)} = {json.dumps(value)}"
            for key, value in sorted(endpoints.items())
        )
    return "\n".join(lines) + "\n"


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _endpoints(value: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_endpoint in (value or {}).items():
        key = str(raw_key).strip().lower()
        endpoint = str(raw_endpoint).strip().rstrip("/")
        if not key or any(character.isspace() for character in key):
            raise ValueError("provider endpoint keys must be non-empty without spaces")
        allowed_schemes = (
            ("http://", "https://", "ws://", "wss://")
            if key.startswith("market-stream")
            else ("https://",)
        )
        if not endpoint.startswith(allowed_schemes):
            requirement = (
                "HTTP(S) or WS(S)" if key.startswith("market-stream") else "HTTPS"
            )
            raise ValueError(f"provider endpoint {key} must use {requirement}")
        result[key] = endpoint
    return result


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item)) for item in value) + "]"
    raise TypeError(f"unsupported connection value: {type(value).__name__}")


def _pending_verification(current_hash: str) -> dict[str, object]:
    return {
        "verification_status": "pending",
        "last_tested_at": None,
        "capabilities_verified": [],
        "observed_permissions": [],
        "warnings": [],
        "error_category": None,
        "tested_configuration_hash": None,
        "current_configuration_hash": current_hash,
    }


def _error_category(error: Exception) -> str:
    name = type(error).__name__.lower()
    text = str(error).lower()
    if "401" in text or "403" in text or "permission" in text:
        return "authentication_or_permission"
    if "timeout" in name or "timeout" in text or "url" in name:
        return "network"
    return "provider_response"


def _sequence(value: object) -> Sequence[object]:
    """Validate a sequence read from TOML, JSON, or provider metadata."""

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _probe_market_connection(
    connection: Mapping[str, object], secrets: Mapping[str, str]
) -> Mapping[str, object]:
    """Run a small provider read; private permission discovery remains explicit."""

    provider = str(connection["provider"])
    endpoints = _string_mapping(connection.get("endpoints"))
    endpoint = endpoints.get("market-query", str(connection["endpoint"]))
    product = next(iter(_sequence(connection.get("products"))), "spot")
    if provider == "binance":
        url = f"{endpoint}/api/v3/exchangeInfo?symbol=BTCUSDT"
        headers = {"X-MBX-APIKEY": secrets.get("api_key", "")}
    elif provider == "okx":
        instrument_type = {
            "spot": "SPOT",
            "swap": "SWAP",
            "futures": "FUTURES",
            "options": "OPTION",
        }.get(str(product), "SPOT")
        url = f"{endpoint}/api/v5/public/instruments?instType={instrument_type}"
        headers = {"OK-ACCESS-KEY": secrets.get("api_key", "")}
    else:
        raise ValueError(f"no generic market probe for provider {provider}")
    request = Request(url, headers={**headers, "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:  # noqa: S310 - HTTPS validated
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("provider response root was not an object")
    return {
        "capabilities": ["market-query"],
        "observed_permissions": [],
        "warnings": ["行情读取已验证；Credential 的账户/交易权限尚未由本测试推断"],
    }


__all__ = [
    "PreparedProviderConnection",
    "ProviderConnectionConfigurationApplication",
]
