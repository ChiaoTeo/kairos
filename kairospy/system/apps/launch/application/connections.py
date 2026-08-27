"""Typed instance connection facts published by Launch runtime composition."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Mapping

from kairospy.system.apps.workspace.application import InstanceWorkspace
from kairospy.primitives.account import AccountId
from kairospy.system.apps.components.application.event_routes import (
    EventTransportRoute,
    event_route_from_manifest,
)


@dataclass(frozen=True, slots=True)
class ComponentConnection:
    component: str
    socket: Path
    snapshot: Path | None = None
    view_root: Path | None = None
    database: Path | None = None
    actor_id: str | None = None
    required_segments: tuple[str, ...] = ()
    broker: str | None = None
    lease_fence: str | None = None
    event_route: EventTransportRoute | None = None


@dataclass(frozen=True, slots=True)
class InstanceConnections:
    accounts: Mapping[AccountId, ComponentConnection]
    market: ComponentConnection | None
    reference: ComponentConnection | None
    risk: ComponentConnection | None
    execution: ComponentConnection | None
    capital: ComponentConnection | None
    workspace_id: str | None = None
    launch_id: str | None = None
    instance_id: str | None = None
    event_routes: Mapping[str, EventTransportRoute] = field(default_factory=dict)


def resolve_instance_connections(instance: InstanceWorkspace) -> InstanceConnections:
    """Parse and validate the component manifest exactly once for a caller."""

    path = instance.component_manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "strategy instance component connection manifest is incomplete"
        ) from error
    if not isinstance(raw, Mapping):
        raise RuntimeError("component connection manifest must be an object")
    schema_version = raw.get("schema_version", 1)
    if schema_version not in {1, 2}:
        raise RuntimeError(f"unsupported component manifest schema: {schema_version}")
    _validate_identity(raw, instance)
    route_values = _mapping(raw.get("event_routes"), "event_routes")
    routes = {
        str(route_id): _event_route(value, str(route_id), instance)
        for route_id, value in route_values.items()
    }
    components = _mapping(raw.get("components"), "components")
    account_values = _mapping(raw.get("accounts"), "accounts")
    accounts = {
        AccountId(str(account_id)): _connection(
            value,
            f"account:{account_id}",
            routes=routes,
            require_event_route=schema_version == 2,
        )
        for account_id, value in account_values.items()
    }
    return InstanceConnections(
        accounts=accounts,
        market=_optional_connection(
            components.get("market"), "market", routes, schema_version == 2
        ),
        reference=_optional_connection(
            components.get("reference"), "reference", routes, schema_version == 2
        ),
        risk=_optional_connection(
            components.get("risk"), "risk", routes, schema_version == 2
        ),
        execution=_optional_connection(
            components.get("execution"), "execution", routes, schema_version == 2
        ),
        capital=_optional_connection(
            components.get("capital"), "capital", routes, schema_version == 2
        ),
        workspace_id=instance.workspace.workspace_id,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
        event_routes=routes,
    )


def _validate_identity(raw: Mapping[str, object], instance: InstanceWorkspace) -> None:
    expected = {
        "launch_id": instance.launch_id,
        "instance_id": instance.instance_id,
        "mode": instance.mode,
    }
    for name, value in expected.items():
        actual = raw.get(name)
        if actual is not None and actual != value:
            raise RuntimeError(
                f"component manifest {name} does not match Strategy instance"
            )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RuntimeError(f"component manifest {name} must be an object")
    return value


def _connection(
    value: object,
    component: str,
    *,
    routes: Mapping[str, EventTransportRoute],
    require_event_route: bool,
) -> ComponentConnection:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{component} connection must be an object")
    socket = value.get("socket")
    if not isinstance(socket, str) or not socket.strip():
        raise RuntimeError(f"{component} connection is missing socket")
    snapshot = value.get("snapshot")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot.strip()):
        raise RuntimeError(f"{component} connection has an invalid snapshot")
    view_root = value.get("view_root")
    if view_root is not None and (
        not isinstance(view_root, str) or not view_root.strip()
    ):
        raise RuntimeError(f"{component} connection has an invalid view_root")
    database = value.get("database")
    if database is not None and (
        not isinstance(database, str) or not database.strip()
    ):
        raise RuntimeError(f"{component} connection has an invalid database")
    actor_id = value.get("actor_id")
    if actor_id is not None and (not isinstance(actor_id, str) or not actor_id.strip()):
        raise RuntimeError(f"{component} connection has an invalid actor_id")
    required_segments = value.get("required_segments", [])
    if not isinstance(required_segments, list) or any(
        not isinstance(segment, str) or not segment.strip()
        for segment in required_segments
    ):
        raise RuntimeError(f"{component} connection has invalid required_segments")
    broker = value.get("broker")
    if broker is not None and (not isinstance(broker, str) or not broker.strip()):
        raise RuntimeError(f"{component} connection has an invalid broker")
    lease_fence = value.get("lease_fence")
    if lease_fence is not None and (
        not isinstance(lease_fence, str) or not lease_fence.strip()
    ):
        raise RuntimeError(f"{component} connection has an invalid lease_fence")
    event_route_ref = value.get("event_route")
    if event_route_ref is not None and (
        not isinstance(event_route_ref, str) or not event_route_ref.strip()
    ):
        raise RuntimeError(f"{component} connection has an invalid event_route")
    if require_event_route and event_route_ref is None:
        raise RuntimeError(f"{component} connection is missing event_route")
    try:
        event_route = (
            None if event_route_ref is None else routes[event_route_ref.strip()]
        )
    except KeyError as error:
        raise RuntimeError(
            f"{component} connection references missing event route {event_route_ref}"
        ) from error
    if component == "reference" and event_route is not None:
        if event_route.scope != "workspace":
            raise RuntimeError("Reference must use the Workspace event route")
    if component.startswith("account:") and event_route is not None:
        if event_route.scope != "instance":
            raise RuntimeError("Account must use the Instance event route")
    if component in {"risk", "execution", "capital"} and event_route is not None:
        if event_route.scope != "instance":
            raise RuntimeError(f"{component} must use the Instance event route")
    return ComponentConnection(
        component=component,
        socket=Path(socket),
        snapshot=None if snapshot is None else Path(snapshot),
        view_root=None if view_root is None else Path(view_root),
        database=None if database is None else Path(database),
        actor_id=None if actor_id is None else actor_id.strip(),
        required_segments=tuple(
            dict.fromkeys(segment.strip() for segment in required_segments)
        ),
        broker=None if broker is None else broker.strip(),
        lease_fence=None if lease_fence is None else lease_fence.strip(),
        event_route=event_route,
    )


def _optional_connection(
    value: object,
    component: str,
    routes: Mapping[str, EventTransportRoute],
    require_event_route: bool,
) -> ComponentConnection | None:
    return (
        None
        if value is None
        else _connection(
            value,
            component,
            routes=routes,
            require_event_route=require_event_route,
        )
    )


def _event_route(
    value: object, route_id: str, instance: InstanceWorkspace
) -> EventTransportRoute:
    route = event_route_from_manifest(value)
    if route.route_id != route_id:
        raise RuntimeError(f"event route {route_id} has a mismatched route_id")
    if route.workspace_id != instance.workspace.workspace_id:
        raise RuntimeError(f"event route {route_id} belongs to another Workspace")
    if route.scope == "instance" and (
        route.launch_id != instance.launch_id
        or route.instance_id != instance.instance_id
    ):
        raise RuntimeError(f"event route {route_id} belongs to another Run Instance")
    return route
