"""Reference-owned configuration and verification for shared data providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    PreparedProviderConnection,
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.apps.workspace.application import Workspace
from kairospy.system.apps.workspace.application import WorkspaceConfigurationTransaction


_MASSIVE_DEFAULT_ENDPOINT = "https://api.massive.com"
_CAPABILITIES = frozenset({"reference", "equity_market", "options"})


@dataclass(frozen=True, slots=True)
class PreparedReferenceProvider:
    workspace: Workspace
    connection: Mapping[str, object]
    document: str
    provider_connection: PreparedProviderConnection

    def stage(self, transaction: WorkspaceConfigurationTransaction) -> None:
        self.provider_connection.stage(transaction)
        transaction.stage_text(self.workspace.paths.manifest, self.document)


@dataclass(frozen=True, slots=True)
class ReferenceProviderConfigurationApplication:
    """Own the Reference binding to the Massive provider connection."""

    workspace: Workspace

    def list(self) -> list[dict[str, Any]]:
        try:
            self._provider_connections().show("massive")
        except KeyError:
            value = self._massive_config()
        else:
            value = {"connection_id": "massive"}
        if not value:
            return []
        return [self.show("massive")]

    def show(self, connection_id: str = "massive") -> dict[str, Any]:
        _require_massive_id(connection_id)
        value = self._connection_config()
        if not value:
            raise KeyError("Massive data connection is not configured")
        credential_id = str(value.get("credential_id") or "")
        issues: list[str] = []
        credential: Mapping[str, Any] = {}
        try:
            credential = CredentialConfigurationApplication(
                self.workspace
            ).resource_snapshot(credential_id)
        except (KeyError, OSError, ValueError):
            issues.append(f"credential does not exist: {credential_id}")
        if credential and credential.get("provider") != "massive":
            issues.append("credential provider must be massive")
        if credential and not credential.get("configured", False):
            issues.extend(
                str(item) for item in cast(Any, credential.get("issues") or ())
            )
        result = {
            "connection_id": "massive",
            "provider": "massive",
            "enabled": bool(value.get("enabled", False)),
            "credential_id": credential_id,
            "endpoint": str(value.get("endpoint") or _MASSIVE_DEFAULT_ENDPOINT),
            "capabilities": list(self._configured_capabilities(value)),
            "configured": bool(value.get("enabled", False)) and not issues,
            "issues": issues,
            "shared_by": ["Reference"],
        }
        return {**result, **self.verification("massive", configuration=result)}

    def configure_massive(
        self,
        *,
        credential_id: str,
        endpoint: str = _MASSIVE_DEFAULT_ENDPOINT,
        capabilities: Sequence[str] = ("reference", "equity_market"),
    ) -> dict[str, Any]:
        prepared = self.prepare_massive(
            credential_id=credential_id,
            endpoint=endpoint,
            capabilities=capabilities,
        )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, "provider-connection:massive"
        )
        prepared.stage(transaction)
        transaction.commit()
        return self.show("massive")

    def prepare_massive(
        self,
        *,
        credential_id: str,
        endpoint: str = _MASSIVE_DEFAULT_ENDPOINT,
        capabilities: Sequence[str] = ("reference", "equity_market"),
        credential_provider: str | None = None,
    ) -> PreparedReferenceProvider:
        credential_id = credential_id.strip()
        endpoint = endpoint.strip().rstrip("/")
        selected = tuple(dict.fromkeys(str(value).strip() for value in capabilities))
        unknown = sorted(set(selected) - _CAPABILITIES)
        if not credential_id:
            raise ValueError("credential_id is required")
        if not endpoint.startswith("https://"):
            raise ValueError("Massive endpoint must use HTTPS")
        if unknown:
            raise ValueError(f"unsupported Massive capabilities: {', '.join(unknown)}")
        if "reference" not in selected:
            raise ValueError("Massive connection must enable Reference catalog access")
        credential: Mapping[str, object] | None = None
        if credential_provider is None:
            credential = CredentialConfigurationApplication(
                self.workspace
            ).resource_snapshot(credential_id)
            credential_provider = str(credential.get("provider") or "")
        if credential_provider != "massive":
            raise ValueError("Massive connection requires a massive credential")
        if credential is not None:
            fields = credential.get("fields")
            if not isinstance(fields, list) or "api_key" not in fields:
                raise ValueError("Massive credential requires an api_key value")

        document = self.workspace.paths.manifest.read_text(encoding="utf-8")
        products = ["equity"]
        purposes = ["reference-catalog"]
        if "equity_market" in selected:
            purposes.append("market-query")
            purposes.append("market-stream")
        if "options" in selected:
            products.append("options")
            if "market-query" not in purposes:
                purposes.append("market-query")
            if "market-stream" not in purposes:
                purposes.append("market-stream")
        provider_connection = self._provider_connections().prepare(
            "massive",
            provider="massive",
            credential_id=credential_id,
            products=products,
            purposes=purposes,
            endpoint=endpoint,
            endpoints={"reference-catalog": endpoint, "market-query": endpoint},
            credential_provider=credential_provider,
            credential_fields=("api_key",),
        )
        document = _set_section(
            document,
            "reference.providers.massive",
            {"enabled": True, "connection_id": "massive"},
        )
        return PreparedReferenceProvider(
            self.workspace,
            {
                "connection_id": "massive",
                "provider": "massive",
                "enabled": True,
                "credential_id": credential_id,
                "endpoint": endpoint,
                "capabilities": list(selected),
                "configured": True,
                "issues": [],
            },
            document,
            provider_connection,
        )

    def test_connection(
        self,
        connection_id: str = "massive",
        *,
        probe: Callable[[str, str], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _require_massive_id(connection_id)
        connection = self.show(connection_id)
        if not connection["configured"]:
            raise ValueError(
                "Massive connection must pass static validation before testing"
            )
        credential_id = str(connection["credential_id"])
        api_key = CredentialConfigurationApplication(self.workspace).resolve_field(
            credential_id, "api_key"
        )
        if not api_key:
            raise ValueError("Massive API key is unavailable")
        result = self.probe(connection, secret=api_key, probe=probe)
        return self.record_probe(connection_id, result)

    def probe(
        self,
        connection: Mapping[str, object],
        *,
        secret: str,
        probe: Callable[[str, str], Mapping[str, Any]] | None = None,
    ) -> dict[str, object]:
        """Test saved or staged Massive settings without persisting evidence."""

        if not secret.strip():
            return {
                "succeeded": False,
                "error_category": "credential_missing",
                "facts": {},
            }
        try:
            facts = dict((probe or _probe_massive)(str(connection["endpoint"]), secret))
        except Exception as error:
            return {
                "succeeded": False,
                "error_category": _probe_error_category(error),
                "facts": {},
            }
        return {"succeeded": True, "error_category": None, "facts": facts}

    def record_probe(
        self, connection_id: str, result: Mapping[str, object]
    ) -> dict[str, Any]:
        connection = self.show(connection_id)
        tested_at = datetime.now(timezone.utc).isoformat()
        facts_value = result.get("facts")
        facts = facts_value if isinstance(facts_value, Mapping) else {}
        if result.get("succeeded") is True:
            evidence = {
                "schema_version": 1,
                "connection_id": connection_id,
                "configuration_hash": self._configuration_fingerprint(connection),
                "result": "verified",
                "tested_at": tested_at,
                "tested": [
                    "API authentication",
                    "AAPL Reference lookup",
                    "SPY hourly bar read",
                ],
                "not_tested": (
                    []
                    if "options" in connection["capabilities"]
                    else ["Options catalog and market data"]
                ),
                "capabilities": list(connection["capabilities"]),
                "samples": {
                    "reference_symbol": str(facts.get("reference_symbol") or "AAPL"),
                    "market_symbol": str(facts.get("market_symbol") or "SPY"),
                    "bar_count": int(facts.get("bar_count") or 1),
                },
            }
        else:
            evidence = {
                "schema_version": 1,
                "connection_id": connection_id,
                "configuration_hash": self._configuration_fingerprint(connection),
                "result": "failed",
                "tested_at": tested_at,
                "tested": [
                    "API authentication",
                    "AAPL Reference lookup",
                    "SPY hourly bar read",
                ],
                "not_tested": ["Options catalog and market data"],
                "capabilities": [],
                "error_category": result.get("error_category") or "provider_response",
            }
        _write_json_atomic(self._evidence_path(connection_id), evidence)
        try:
            self._provider_connections().record_probe_result(
                connection_id,
                succeeded=result.get("succeeded") is True,
                capabilities=("reference-catalog", "market-query"),
                error_category=str(
                    result.get("error_category") or "provider_response"
                ),
            )
        except KeyError:
            # Legacy manifest-only configurations keep their bounded read path
            # until the deterministic migration creates a connection profile.
            pass
        return self.verification(connection_id)

    def set_enabled(
        self, connection_id: str = "massive", *, enabled: bool
    ) -> dict[str, Any]:
        """Enable or disable the shared connection without changing its credential."""

        _require_massive_id(connection_id)
        try:
            self._provider_connections().set_enabled(connection_id, enabled=enabled)
        except KeyError:
            current = dict(self._massive_config())
            if not current:
                raise KeyError("Massive data connection is not configured")
            current["enabled"] = enabled
        else:
            current = {"enabled": enabled, "connection_id": connection_id}
        document = self.workspace.paths.manifest.read_text(encoding="utf-8")
        document = _set_section(document, "reference.providers.massive", current)
        _write_atomic(self.workspace.paths.manifest, document)
        return self.show(connection_id)

    def delete(self, connection_id: str = "massive") -> dict[str, str]:
        """Remove owner configuration and evidence, retaining the credential."""

        _require_massive_id(connection_id)
        try:
            self._provider_connections().show(connection_id)
        except KeyError:
            has_connection = False
        else:
            has_connection = True
        if not has_connection and not self._massive_config():
            raise KeyError("Massive data connection is not configured")
        document = self.workspace.paths.manifest.read_text(encoding="utf-8")
        document = _set_section(document, "reference.providers.massive", None)
        _write_atomic(self.workspace.paths.manifest, document)
        if has_connection:
            self._provider_connections().delete(connection_id)
        self._evidence_path(connection_id).unlink(missing_ok=True)
        return {"connection_id": connection_id, "status": "deleted"}

    def verification(
        self,
        connection_id: str = "massive",
        *,
        configuration: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_massive_id(connection_id)
        path = self._evidence_path(connection_id)
        if not path.is_file():
            current = configuration or self._base_configuration()
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested": [],
                "not_tested": [],
                "tested_configuration_hash": None,
                "current_configuration_hash": self._configuration_fingerprint(current),
            }
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = configuration or self._base_configuration()
            return {
                "verification_status": "pending",
                "last_tested_at": None,
                "tested": [],
                "not_tested": [],
                "tested_configuration_hash": None,
                "current_configuration_hash": self._configuration_fingerprint(current),
            }
        current = configuration or self._base_configuration()
        status = str(evidence.get("result") or "failed")
        if evidence.get("configuration_hash") != self._configuration_fingerprint(
            current
        ):
            status = "retest_required"
        return {
            "verification_status": status,
            "last_tested_at": evidence.get("tested_at"),
            "tested": list(evidence.get("tested") or ()),
            "not_tested": list(evidence.get("not_tested") or ()),
            "capabilities_verified": list(evidence.get("capabilities") or ()),
            "samples": dict(evidence.get("samples") or {}),
            "error_category": evidence.get("error_category"),
            "tested_configuration_hash": evidence.get("configuration_hash"),
            "current_configuration_hash": self._configuration_fingerprint(current),
        }

    def resource_snapshot(self, connection_id: str = "massive") -> dict[str, Any]:
        connection = self.show(connection_id)
        credential = CredentialConfigurationApplication(self.workspace).show(
            str(connection["credential_id"])
        )
        safe = {
            key: connection.get(key)
            for key in (
                "connection_id",
                "provider",
                "enabled",
                "credential_id",
                "endpoint",
                "capabilities",
                "shared_by",
                "verification_status",
                "last_tested_at",
                "tested",
                "not_tested",
            )
        }
        safe["credential_identity"] = {
            "provider": credential.get("provider"),
            "role": credential.get("role"),
            "fields": credential.get("fields", []),
        }
        safe["resource_hash"] = self._configuration_fingerprint(connection)
        return safe

    def _base_configuration(self) -> dict[str, Any]:
        value = self._connection_config()
        return {
            "connection_id": "massive",
            "provider": "massive",
            "enabled": bool(value.get("enabled", False)),
            "credential_id": str(value.get("credential_id") or ""),
            "endpoint": str(value.get("endpoint") or _MASSIVE_DEFAULT_ENDPOINT),
            "capabilities": list(self._configured_capabilities(value)),
        }

    def _configuration_fingerprint(self, value: Mapping[str, Any]) -> str:
        credential_id = str(value.get("credential_id") or "")
        try:
            credential = CredentialConfigurationApplication(self.workspace).show(
                credential_id
            )
        except (KeyError, OSError, ValueError):
            credential = {"credential_id": credential_id, "missing": True}
        payload = {
            "connection_id": value.get("connection_id"),
            "provider": "massive",
            "enabled": value.get("enabled"),
            "credential_id": credential_id,
            "endpoint": value.get("endpoint"),
            "capabilities": sorted(
                str(item) for item in value.get("capabilities") or ()
            ),
            "credential": {
                "provider": credential.get("provider"),
                "role": credential.get("role"),
                "fields": credential.get("fields", []),
                "resource_hash": credential.get("resource_hash"),
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _massive_config(self) -> Mapping[str, Any]:
        value = tomllib.loads(self.workspace.paths.manifest.read_text(encoding="utf-8"))
        reference = value.get("reference")
        providers = (
            reference.get("providers") if isinstance(reference, Mapping) else None
        )
        massive = providers.get("massive") if isinstance(providers, Mapping) else None
        return massive if isinstance(massive, Mapping) else {}

    def _connection_config(self) -> Mapping[str, Any]:
        try:
            return self._provider_connections().show("massive")
        except KeyError:
            return self._massive_config()

    def _configured_capabilities(
        self, connection: Mapping[str, Any] | None = None
    ) -> tuple[str, ...]:
        if connection is not None and connection.get("products"):
            products = {str(item) for item in connection.get("products") or ()}
            purposes = {str(item) for item in connection.get("purposes") or ()}
            result = ["reference"] if "reference-catalog" in purposes else []
            if "equity" in products:
                result.append("equity_market")
            if "options" in products:
                result.append("options")
            return tuple(result)
        value = tomllib.loads(self.workspace.paths.manifest.read_text(encoding="utf-8"))
        result = ["reference"]
        market = value.get("market")
        providers = market.get("providers") if isinstance(market, Mapping) else None
        if isinstance(providers, list):
            products = {
                str(item.get("product"))
                for item in providers
                if isinstance(item, Mapping)
                and item.get("type") == "massive"
                and item.get("enabled", True)
            }
            if "equity" in products:
                result.append("equity_market")
            if "options" in products:
                result.append("options")
        return tuple(result)

    def _provider_connections(self) -> ProviderConnectionConfigurationApplication:
        return ProviderConnectionConfigurationApplication(self.workspace)

    def _evidence_path(self, connection_id: str) -> Path:
        return self.workspace.paths.child(
            "state", "configuration", "data-providers", f"{connection_id}.json"
        )


def _probe_massive(endpoint: str, api_key: str) -> Mapping[str, Any]:
    reference = _get_json(
        f"{endpoint}/v3/reference/tickers/AAPL",
        api_key,
    )
    result = reference.get("results")
    if not isinstance(result, Mapping) or str(result.get("ticker") or "") != "AAPL":
        raise ValueError("Massive Reference response did not contain AAPL")
    now = datetime.now(timezone.utc)
    start = int((now - timedelta(days=7)).timestamp() * 1000)
    end = int(now.timestamp() * 1000)
    query = urlencode({"adjusted": "true", "sort": "desc", "limit": "1"})
    bars = _get_json(
        f"{endpoint}/v2/aggs/ticker/SPY/range/1/hour/{start}/{end}?{query}",
        api_key,
    )
    rows = bars.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Massive Market response did not contain a SPY hourly bar")
    return {"reference_symbol": "AAPL", "market_symbol": "SPY", "bar_count": len(rows)}


def _get_json(url: str, api_key: str) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "kairos-data-provider-probe/1",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - configured HTTPS only
        value = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("Massive response root was not an object")
    return value


def _set_section(
    document: str, section: str, values: Mapping[str, object] | None
) -> str:
    header = f"[{section}]"
    pattern = re.compile(rf"(?ms)^\[{re.escape(section)}\]\s*\n.*?(?=^\[|\Z)")
    replacement = ""
    if values is not None:
        lines = [header]
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        replacement = "\n".join(lines) + "\n\n"
    if pattern.search(document):
        return pattern.sub(replacement, document, count=1)
    if not replacement:
        return document
    return document.rstrip() + "\n\n" + replacement


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unsupported manifest value: {type(value).__name__}")


def _write_atomic(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _probe_error_category(error: Exception) -> str:
    if isinstance(error, HTTPError):
        if error.code in {401, 403}:
            return "authentication_or_entitlement"
        if error.code == 429:
            return "rate_limited"
        return "provider_http"
    if isinstance(error, (URLError, TimeoutError)):
        return "network"
    return "invalid_response"


def _require_massive_id(value: str) -> None:
    if value.strip() != "massive":
        raise ValueError(
            "the first release supports the shared Massive connection only"
        )


__all__ = ["ReferenceProviderConfigurationApplication"]
