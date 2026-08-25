from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
from typing import Mapping

from kairospy.system.apps.workspace.application import InstanceWorkspace, Workspace
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.strategy import StrategyIdentity, StrategyLogger

from ..application.application import NotificationApplication
from ..application.models import NotificationDestination
from ..services import (
    AppriseSender,
    NotificationDeliveryRuntime,
    RecordingSender,
)


class NotificationConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NotificationProcessComposition:
    application: NotificationApplication
    runtime: NotificationDeliveryRuntime
    config_hash: str | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _DestinationRecord:
    destination_id: str
    sender: str
    credential_id: str | None
    enabled: bool
    settings: Mapping[str, str]


def compose_notifications(
    *,
    workspace: Workspace,
    instance: InstanceWorkspace,
    identity: StrategyIdentity,
    mode: str,
    config: Mapping[str, object],
    logger: StrategyLogger,
) -> NotificationProcessComposition:
    enabled = _boolean(config.get("enabled", False), "notifications.enabled")
    required = _boolean(config.get("required", False), "notifications.required")
    routes = _routes(config.get("routes", {}))
    referenced_destination_ids = tuple(
        dict.fromkeys(
            destination
            for destinations in routes.values()
            for destination in destinations
        )
    )
    if len(referenced_destination_ids) > 64:
        raise NotificationConfigError(
            "notifications may reference at most 64 unique destinations"
        )
    defaults = _string_tuple(
        config.get("default_routes", []), "notifications.default_routes"
    )
    unknown_defaults = tuple(route for route in defaults if route not in routes)
    if unknown_defaults:
        raise NotificationConfigError(
            "notifications.default_routes reference unknown routes: "
            + ",".join(unknown_defaults)
        )
    lifecycle_routes = _string_tuple(
        config.get("lifecycle_routes", []), "notifications.lifecycle_routes"
    )
    unknown_lifecycle = tuple(
        route for route in lifecycle_routes if route not in routes
    )
    if unknown_lifecycle:
        raise NotificationConfigError(
            "notifications.lifecycle_routes reference unknown routes: "
            + ",".join(unknown_lifecycle)
        )
    if enabled and not routes:
        raise NotificationConfigError(
            "enabled notifications require at least one route"
        )
    queue_capacity = _integer(
        config.get("queue_capacity", 256), "notifications.queue_capacity", 1, 100_000
    )
    shutdown_grace = _number(
        config.get("shutdown_grace_seconds", 5),
        "notifications.shutdown_grace_seconds",
        0,
        300,
    )
    runtime_identity = {
        "workspace_id": workspace.identity.workspace_id,
        "launch_id": identity.launch_id,
        "instance_id": identity.instance_id,
        "strategy_id": identity.strategy_id,
        "mode": mode,
    }
    if not enabled:
        runtime = NotificationDeliveryRuntime(
            identity=runtime_identity,
            routes={},
            default_routes=(),
            destinations={},
            senders={},
            enabled=False,
            logger=logger,
        )
        return NotificationProcessComposition(
            NotificationApplication(runtime), runtime, None
        )

    records, destination_config_hash = _load_destinations(
        workspace.paths.notification_config()
    )
    config_hash = _notification_resources_hash(
        workspace, records, destination_config_hash, mode=mode
    )
    expected_config_hash = str(config.get("workspace_config_hash", "")).strip()
    if expected_config_hash and expected_config_hash != config_hash:
        raise NotificationConfigError(
            "Workspace notification configuration changed after this Launch instance "
            "was assembled; create a new instance"
        )
    referenced = referenced_destination_ids
    issues: list[str] = []
    destinations: dict[str, NotificationDestination] = {}
    if mode == "backtest":
        for destination_id in referenced:
            if destination_id not in records:
                issues.append(f"notification destination not found: {destination_id}")
                continue
            destinations[destination_id] = NotificationDestination(
                destination_id, "recording"
            )
        recording_path = instance.artifact("notifications.jsonl")
        recording_path.unlink(missing_ok=True)
        recording = RecordingSender(recording_path)
        senders = {destination_id: recording for destination_id in destinations}
    else:
        for destination_id in referenced:
            record = records.get(destination_id)
            if record is None:
                issues.append(f"notification destination not found: {destination_id}")
                continue
            try:
                destinations[destination_id] = _resolve_destination(workspace, record)
            except NotificationConfigError as error:
                issues.append(str(error))
        senders = {}
        for destination_id, destination in tuple(destinations.items()):
            try:
                senders[destination_id] = AppriseSender(destination)
            except ValueError as error:
                issues.append(str(error))
                destinations.pop(destination_id)
    if issues and required:
        raise NotificationConfigError("; ".join(issues))
    active_routes = {
        route: tuple(
            destination
            for destination in route_destinations
            if destination in destinations
        )
        for route, route_destinations in routes.items()
    }
    runtime = NotificationDeliveryRuntime(
        identity=runtime_identity,
        routes=active_routes,
        default_routes=defaults,
        destinations=destinations,
        senders=senders,
        queue_capacity=queue_capacity,
        shutdown_grace_seconds=shutdown_grace,
        logger=logger,
        initial_state="degraded" if issues else "healthy",
        journal_path=instance.log("notification", "delivery.jsonl"),
        health_path=instance.health("notification"),
        config_hash=config_hash,
        config_issues=tuple(issues),
    )
    return NotificationProcessComposition(
        NotificationApplication(runtime),
        runtime,
        config_hash,
        tuple(issues),
    )


def validate_notification_resources(
    workspace: Workspace,
    config: Mapping[str, object],
    *,
    mode: str,
    resolve_secrets: bool = False,
) -> tuple[str, ...]:
    if not config.get("enabled", False):
        return ()
    try:
        routes = _routes(config.get("routes", {}))
        records, _ = _load_destinations(workspace.paths.notification_config())
    except (NotificationConfigError, OSError) as error:
        return (str(error),)
    issues: list[str] = []
    for destination_id in dict.fromkeys(
        destination for values in routes.values() for destination in values
    ):
        record = records.get(destination_id)
        if record is None:
            issues.append(f"notification destination not found: {destination_id}")
            continue
        if not record.enabled:
            issues.append(f"notification destination is disabled: {destination_id}")
            continue
        if record.sender not in {"feishu", "telegram"}:
            issues.append(
                f"notification destination {destination_id} has unsupported sender: "
                f"{record.sender}"
            )
            continue
        if record.sender == "telegram" and not record.settings.get("chat_id"):
            issues.append(f"notification destination {destination_id} requires chat_id")
        if mode != "backtest":
            if not record.credential_id:
                issues.append(
                    f"notification destination {destination_id} requires credential_id"
                )
            elif not _credential_file(workspace, record.credential_id).is_file():
                issues.append(
                    f"notification credential not found: {record.credential_id}"
                )
            else:
                try:
                    credential = _load_credential(workspace, record.credential_id)
                    provider = str(
                        credential.get("provider", credential.get("broker", ""))
                    ).lower()
                    if provider != record.sender:
                        issues.append(
                            f"notification credential {record.credential_id} provider "
                            f"{provider!r} does not match sender {record.sender!r}"
                        )
                    elif resolve_secrets:
                        AppriseSender(_resolve_destination(workspace, record))
                except (NotificationConfigError, ValueError) as error:
                    issues.append(str(error))
    return tuple(issues)


def validate_workspace_notifications(
    workspace: Workspace, *, mode: str = "paper"
) -> dict[str, object]:
    path = workspace.paths.notification_config()
    try:
        records, _ = _load_destinations(path)
    except NotificationConfigError as error:
        return {"valid": False, "path": str(path), "issues": [str(error)]}
    if not records:
        return {"valid": True, "path": str(path), "issues": []}
    config = {
        "enabled": True,
        "required": True,
        "default_routes": ["all"],
        "routes": {"all": list(records)},
    }
    issues = validate_notification_resources(
        workspace, config, mode=mode, resolve_secrets=mode != "backtest"
    )
    return {"valid": not issues, "path": str(path), "issues": list(issues)}


async def test_notification_destination(
    workspace: Workspace, destination_id: str
) -> dict[str, object]:
    """Compatibility entry point for callers not yet using the admin application."""

    from ..application.admin import NotificationAdminApplication

    return await NotificationAdminApplication(workspace).test_destination(
        destination_id
    )


def _load_destinations(path: Path) -> tuple[dict[str, _DestinationRecord], str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise NotificationConfigError(
            f"Workspace notification config does not exist: {path}"
        ) from error
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise NotificationConfigError(
            f"invalid Workspace notification config: {path}: {error}"
        ) from error
    if value.get("version", 1) != 1:
        raise NotificationConfigError("unsupported notification config version")
    table = value.get("destinations")
    if not isinstance(table, Mapping):
        raise NotificationConfigError("notification config requires [destinations]")
    records: dict[str, _DestinationRecord] = {}
    for raw_id, raw_record in table.items():
        destination_id = str(raw_id).strip()
        if not destination_id or not isinstance(raw_record, Mapping):
            raise NotificationConfigError(f"invalid notification destination: {raw_id}")
        sender = str(raw_record.get("sender", "")).strip().lower()
        credential = raw_record.get("credential_id")
        credential_id = None if credential is None else str(credential).strip()
        enabled = raw_record.get("enabled", True)
        if not isinstance(enabled, bool):
            raise NotificationConfigError(
                f"notification destination {destination_id} enabled must be a boolean"
            )
        settings = {
            str(key): str(item)
            for key, item in raw_record.items()
            if key not in {"sender", "credential_id", "enabled"}
            and isinstance(item, (str, int, float, bool))
        }
        forbidden = {
            "webhook_url",
            "bot_token",
            "signing_secret",
            "secret",
            "token",
        }
        present = forbidden.intersection(str(key).lower() for key in raw_record)
        if present:
            raise NotificationConfigError(
                f"notification destination {destination_id} contains inline secret: "
                f"{sorted(present)[0]}"
            )
        records[destination_id] = _DestinationRecord(
            destination_id, sender, credential_id, enabled, settings
        )
    return records, hashlib.sha256(raw).hexdigest()


def _resolve_destination(
    workspace: Workspace, record: _DestinationRecord
) -> NotificationDestination:
    if record.sender not in {"feishu", "telegram"}:
        raise NotificationConfigError(
            f"notification destination {record.destination_id} has unsupported sender: "
            f"{record.sender}"
        )
    if not record.enabled:
        raise NotificationConfigError(
            f"notification destination is disabled: {record.destination_id}"
        )
    if not record.credential_id:
        raise NotificationConfigError(
            f"notification destination {record.destination_id} requires credential_id"
        )
    credential = _load_credential(workspace, record.credential_id)
    provider = str(credential.get("provider", credential.get("broker", ""))).lower()
    if provider != record.sender:
        raise NotificationConfigError(
            f"notification credential {record.credential_id} provider {provider!r} "
            f"does not match sender {record.sender!r}"
        )
    if record.sender == "feishu":
        signing_secret = _credential_value(
            workspace,
            record.credential_id,
            "signing_secret",
            credential,
            required=False,
        )
        if signing_secret:
            raise NotificationConfigError(
                f"notification credential {record.credential_id} enables Feishu "
                "signing, which the Apprise adapter does not support"
            )
        secrets = {
            "webhook_url": _credential_value(
                workspace, record.credential_id, "webhook_url", credential
            ),
        }
    else:
        if not record.settings.get("chat_id"):
            raise NotificationConfigError(
                f"notification destination {record.destination_id} requires chat_id"
            )
        secrets = {
            "bot_token": _credential_value(
                workspace, record.credential_id, "bot_token", credential
            )
        }
    return NotificationDestination(
        record.destination_id,
        record.sender,  # type: ignore[arg-type]
        credential_id=record.credential_id,
        settings=record.settings,
        secrets=secrets,
    )


def _load_credential(workspace: Workspace, credential_id: str) -> Mapping[str, object]:
    path = _credential_file(workspace, credential_id)
    if not path.is_file():
        raise NotificationConfigError(
            f"notification credential not found: {credential_id}"
        )
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise NotificationConfigError(
            f"invalid notification credential {credential_id}: {error}"
        ) from error
    table = value.get("credential", value)
    if not isinstance(table, Mapping):
        raise NotificationConfigError(
            f"notification credential {credential_id} must be a TOML table"
        )
    actual_id = str(table.get("id", credential_id))
    if actual_id != credential_id:
        raise NotificationConfigError(
            f"notification credential identity mismatch: {actual_id} != {credential_id}"
        )
    return table


def _credential_file(workspace: Workspace, credential_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", credential_id) or "unnamed"
    return workspace.paths.credentials_root() / f"{safe}.toml"


def _credential_value(
    workspace: Workspace,
    credential_id: str,
    field: str,
    values: Mapping[str, object],
    *,
    required: bool = True,
) -> str:
    del workspace
    credential_values = values.get("values")
    raw = (
        credential_values.get(field) if isinstance(credential_values, Mapping) else None
    )
    value = raw.strip() if isinstance(raw, str) else ""
    if required and not value:
        raise NotificationConfigError(
            f"notification credential {credential_id} is missing {field}"
        )
    return value


def notification_config_hash(workspace: Workspace, *, mode: str = "paper") -> str:
    """Return the content identity pinned into a Launch instance."""

    records, config_hash = _load_destinations(workspace.paths.notification_config())
    return _notification_resources_hash(workspace, records, config_hash, mode=mode)


def _notification_resources_hash(
    workspace: Workspace,
    records: Mapping[str, _DestinationRecord],
    destination_config_hash: str,
    *,
    mode: str,
) -> str:
    if mode == "backtest":
        return destination_config_hash
    resources: list[tuple[str, str]] = [("destinations", destination_config_hash)]
    credentials = CredentialConfigurationApplication(workspace)
    for destination_id, record in sorted(records.items()):
        if not record.credential_id:
            resources.append((f"credential:{destination_id}", "missing"))
            continue
        try:
            value = str(
                credentials.resource_snapshot(record.credential_id)["resource_hash"]
            )
        except KeyError:
            value = "missing"
        resources.append((f"credential:{record.credential_id}", value))
    return hashlib.sha256(
        json.dumps(resources, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _routes(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise NotificationConfigError("notifications.routes must be a table")
    result: dict[str, tuple[str, ...]] = {}
    for route, destinations in value.items():
        name = str(route).strip()
        if not name:
            raise NotificationConfigError("notification route name is required")
        result[name] = _string_tuple(
            destinations, f"notifications.routes.{name}", non_empty=True
        )
    return result


def _string_tuple(
    value: object, name: str, *, non_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise NotificationConfigError(f"{name} must be an array of strings")
    result = tuple(dict.fromkeys(item.strip() for item in value))
    if non_empty and not result:
        raise NotificationConfigError(f"{name} must not be empty")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise NotificationConfigError(f"{name} must be a boolean")
    return value


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise NotificationConfigError(
            f"{name} must be an integer from {minimum} to {maximum}"
        )
    return value


def _number(value: object, name: str, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not minimum <= float(value) <= maximum
    ):
        raise NotificationConfigError(
            f"{name} must be a number from {minimum} to {maximum}"
        )
    return float(value)


__all__ = [
    "NotificationConfigError",
    "NotificationProcessComposition",
    "compose_notifications",
    "notification_config_hash",
    "test_notification_destination",
    "validate_notification_resources",
    "validate_workspace_notifications",
]
