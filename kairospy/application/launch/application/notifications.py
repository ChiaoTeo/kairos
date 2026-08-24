from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from kairospy.application.workspace import Workspace

from .wizard import build_and_validate, load_values


@dataclass(frozen=True, slots=True)
class LaunchNotificationConfigurationApplication:
    """Launch-owned use cases for notification route selection."""

    workspace: Workspace

    def list(self) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for path in sorted((self.workspace.paths.config / "launches").glob("*.toml")):
            try:
                config = load_values(path)
            except ValueError:
                continue
            launch = config.get("launch", {})
            notifications = config.get("notifications", {})
            if not isinstance(launch, Mapping) or not isinstance(
                notifications, Mapping
            ):
                continue
            values.append(
                {
                    "launch_id": str(launch.get("id", path.stem)),
                    "path": str(path),
                    "mode": str(launch.get("mode", "")),
                    "notifications_enabled": bool(notifications.get("enabled", False)),
                    "routes": dict(notifications.get("routes", {}))
                    if isinstance(notifications.get("routes"), Mapping)
                    else {},
                }
            )
        return values

    def references_to(self, destination_id: str) -> list[dict[str, object]]:
        references: list[dict[str, object]] = []
        for launch in self.list():
            routes = launch.get("routes", {})
            if not isinstance(routes, Mapping):
                continue
            matched = [
                str(route)
                for route, destinations in routes.items()
                if isinstance(destinations, list) and destination_id in destinations
            ]
            if matched:
                references.append(
                    {
                        "launch_id": launch["launch_id"],
                        "path": launch["path"],
                        "routes": matched,
                    }
                )
        return references

    def attach(
        self,
        launch_id: str,
        destination_id: str,
        *,
        route: str,
        default: bool = False,
        lifecycle: bool = False,
        required: bool = True,
    ) -> dict[str, Any]:
        path = self._path(launch_id)
        values = load_values(path)
        route = _route_name(route)
        notifications = _table(values, "notifications")
        notifications["enabled"] = True
        notifications["required"] = required
        notifications.setdefault("queue_capacity", 256)
        notifications.setdefault("shutdown_grace_seconds", 5)
        routes = _table(notifications, "routes")
        current = routes.get(route, [])
        if not isinstance(current, list):
            raise ValueError(f"notifications.routes.{route} must be an array")
        routes[route] = list(dict.fromkeys([*current, destination_id]))
        if default:
            defaults = notifications.get("default_routes", [])
            if not isinstance(defaults, list):
                raise ValueError("notifications.default_routes must be an array")
            notifications["default_routes"] = list(dict.fromkeys([*defaults, route]))
        else:
            notifications.setdefault("default_routes", [])
        if lifecycle:
            lifecycle_routes = notifications.get("lifecycle_routes", [])
            if not isinstance(lifecycle_routes, list):
                raise ValueError("notifications.lifecycle_routes must be an array")
            notifications["lifecycle_routes"] = list(
                dict.fromkeys([*lifecycle_routes, route])
            )
        return build_and_validate(path, values, self.workspace.paths.root)

    def detach(self, launch_id: str, destination_id: str) -> dict[str, Any]:
        path = self._path(launch_id)
        values = load_values(path)
        notifications = _table(values, "notifications")
        routes = _table(notifications, "routes")
        for route, destinations in tuple(routes.items()):
            if not isinstance(destinations, list):
                continue
            remaining = [item for item in destinations if item != destination_id]
            if remaining:
                routes[route] = remaining
            else:
                routes.pop(route)
                for name in ("default_routes", "lifecycle_routes"):
                    selected = notifications.get(name, [])
                    if isinstance(selected, list):
                        notifications[name] = [
                            item for item in selected if item != route
                        ]
        if not routes:
            notifications["enabled"] = False
            notifications["required"] = False
        return build_and_validate(path, values, self.workspace.paths.root)

    def _path(self, launch_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", launch_id):
            raise ValueError("launch_id must be a path-safe name")
        path = self.workspace.paths.launch_config(launch_id)
        if not path.is_file():
            raise FileNotFoundError(f"launch config does not exist: {path}")
        return path


def _table(values: dict[str, Any], key: str) -> dict[str, Any]:
    value = values.get(key)
    if value is None:
        value = {}
        values[key] = value
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a TOML table")
    return value


def _route_name(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", normalized):
        raise ValueError("notification route must be a path-safe name")
    return normalized


__all__ = ["LaunchNotificationConfigurationApplication"]
