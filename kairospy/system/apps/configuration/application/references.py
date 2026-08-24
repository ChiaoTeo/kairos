"""System composition queries for cross-owner configuration references."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from kairospy.system.domain.workspace import Workspace


@dataclass(frozen=True, slots=True)
class ConfigurationReferenceApplication:
    """Aggregate references without transferring resource ownership."""

    workspace: Workspace

    def resource_references(
        self, resource_kind: str, resource_id: str
    ) -> list[dict[str, str]]:
        """Return stable cross-resource references for surfaces."""

        resolvers = {
            "account": self.account_references,
            "market_data": self.data_provider_references,
            "ai_model": self.model_connection_references,
            "notification": self.destination_references,
            "credential": self.credential_references,
        }
        try:
            resolver = resolvers[resource_kind]
        except KeyError as error:
            raise ValueError(f"unsupported resource kind: {resource_kind}") from error
        return resolver(resource_id)

    def deletion_impact(
        self, resource_kind: str, resource_id: str
    ) -> dict[str, object]:
        references = self.resource_references(resource_kind, resource_id)
        return {
            "resource_kind": resource_kind,
            "resource_id": _required_id(resource_id),
            "allowed": not references,
            "references": references,
            "reference_count": len(references),
            "reason": None if not references else "resource_is_referenced",
        }

    def credential_references(self, credential_id: str) -> list[dict[str, str]]:
        credential_id = _required_id(credential_id)
        result: list[dict[str, str]] = []
        for path in self._configuration_documents(exclude_credentials=True):
            value = _read(path)
            for location, item in _walk(value):
                if (
                    location[-1:] in {("credential_id",), ("credential",), ("ref",)}
                    and item == credential_id
                ):
                    if location[-1] == "ref" and "credentials" not in location:
                        continue
                    result.append(_reference(path, location, self.workspace.paths.root))
        return _deduplicate(result)

    def account_references(self, account_id: str) -> list[dict[str, str]]:
        account_id = _required_id(account_id)
        result: list[dict[str, str]] = []
        for path in self._launch_documents():
            value = _read(path)
            account = value.get("account")
            if isinstance(account, Mapping) and account.get("ref") == account_id:
                result.append(
                    _reference(path, ("account", "ref"), self.workspace.paths.root)
                )
            accounts = value.get("accounts")
            if isinstance(accounts, Mapping):
                for alias, entry in accounts.items():
                    if isinstance(entry, Mapping) and entry.get("ref") == account_id:
                        result.append(
                            _reference(
                                path,
                                ("accounts", str(alias), "ref"),
                                self.workspace.paths.root,
                            )
                        )
        return _deduplicate(result)

    def model_connection_references(self, connection_id: str) -> list[dict[str, str]]:
        connection_id = _required_id(connection_id)
        result: list[dict[str, str]] = []
        for path in self._launch_documents():
            value = _read(path)
            agent = value.get("agent")
            model = agent.get("model") if isinstance(agent, Mapping) else None
            if not isinstance(model, Mapping):
                continue
            if model.get("connection", model.get("credential")) == connection_id:
                location = (
                    ("agent", "model", "connection")
                    if "connection" in model
                    else ("agent", "model", "credential")
                )
                result.append(_reference(path, location, self.workspace.paths.root))
        return _deduplicate(result)

    def destination_references(self, destination_id: str) -> list[dict[str, str]]:
        destination_id = _required_id(destination_id)
        result: list[dict[str, str]] = []
        for path in self._launch_documents():
            value = _read(path)
            notifications = value.get("notifications")
            routes = (
                notifications.get("routes")
                if isinstance(notifications, Mapping)
                else None
            )
            if not isinstance(routes, Mapping):
                continue
            for route, destinations in routes.items():
                if isinstance(destinations, list) and destination_id in destinations:
                    result.append(
                        _reference(
                            path,
                            ("notifications", "routes", str(route)),
                            self.workspace.paths.root,
                        )
                    )
        return _deduplicate(result)

    def data_provider_references(self, connection_id: str) -> list[dict[str, str]]:
        connection_id = _required_id(connection_id)
        result: list[dict[str, str]] = []
        for path in self._launch_documents():
            value = _read(path)
            for mode in ("paper", "live"):
                mode_config = value.get(mode)
                market = (
                    mode_config.get("market")
                    if isinstance(mode_config, Mapping)
                    else None
                )
                if (
                    isinstance(market, Mapping)
                    and market.get("profile") == connection_id
                ):
                    result.append(
                        _reference(
                            path,
                            (mode, "market", "profile"),
                            self.workspace.paths.root,
                        )
                    )
        return _deduplicate(result)

    def _configuration_documents(
        self, *, exclude_credentials: bool
    ) -> tuple[Path, ...]:
        paths = list(self._launch_documents())
        paths.extend(sorted((self.workspace.paths.config / "accounts").glob("*.toml")))
        notification = self.workspace.paths.notification_config()
        if notification.is_file():
            paths.append(notification)
        if self.workspace.paths.manifest.is_file():
            paths.append(self.workspace.paths.manifest)
        if not exclude_credentials:
            paths.extend(
                sorted((self.workspace.paths.config / "credentials").glob("*.toml"))
            )
        return tuple(dict.fromkeys(paths))

    def _launch_documents(self) -> tuple[Path, ...]:
        root = self.workspace.paths.config / "launches"
        return tuple(
            [*sorted(root.glob("*.toml")), *sorted((root / ".drafts").glob("*.toml"))]
            if root.is_dir()
            else ()
        )


def _read(path: Path) -> Mapping[str, Any]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _walk(
    value: object, prefix: tuple[str, ...] = ()
) -> tuple[tuple[tuple[str, ...], object], ...]:
    result: list[tuple[tuple[str, ...], object]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            location = (*prefix, str(key))
            result.append((location, item))
            result.extend(_walk(item, location))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_walk(item, (*prefix, str(index))))
    return tuple(result)


def _reference(path: Path, location: tuple[str, ...], root: Path) -> dict[str, str]:
    try:
        source = str(path.relative_to(root))
    except ValueError:
        source = str(path)
    return {
        "source": source,
        "location": ".".join(location),
        "document_state": "draft" if "/.drafts/" in f"/{source}" else "published",
    }


def _deduplicate(values: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for value in values:
        key = (value["source"], value["location"])
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _required_id(value: str) -> str:
    value = value.strip()
    if not value or "/" in value or "\\" in value:
        raise ValueError("resource id must be a path-safe value")
    return value


__all__ = ["ConfigurationReferenceApplication"]
