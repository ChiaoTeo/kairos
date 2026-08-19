"""Typed instance endpoint facts published by Launch runtime composition."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from ...workspace import InstanceWorkspace
from ....domain_types import AccountId


@dataclass(frozen=True, slots=True)
class ComponentEndpoint:
    component: str
    socket: Path
    snapshot: Path | None = None
    view_root: Path | None = None
    required_segments: tuple[str, ...] = ()
    broker: str | None = None
    lease_fence: str | None = None


@dataclass(frozen=True, slots=True)
class InstanceEndpoints:
    accounts: Mapping[AccountId, ComponentEndpoint]
    risk: ComponentEndpoint | None
    execution: ComponentEndpoint | None
    capital: ComponentEndpoint | None


def resolve_instance_endpoints(instance: InstanceWorkspace) -> InstanceEndpoints:
    """Parse and validate the component manifest exactly once for a caller."""

    path = instance.component_manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "strategy instance component endpoint manifest is incomplete"
        ) from error
    if not isinstance(raw, Mapping):
        raise RuntimeError("component endpoint manifest must be an object")
    schema_version = raw.get("schema_version", 1)
    if schema_version != 1:
        raise RuntimeError(f"unsupported component manifest schema: {schema_version}")
    _validate_identity(raw, instance)
    components = _mapping(raw.get("components"), "components")
    account_values = _mapping(raw.get("accounts"), "accounts")
    accounts = {
        AccountId(str(account_id)): _endpoint(value, f"account:{account_id}")
        for account_id, value in account_values.items()
    }
    return InstanceEndpoints(
        accounts=accounts,
        risk=_optional_endpoint(components.get("risk"), "risk"),
        execution=_optional_endpoint(components.get("execution"), "execution"),
        capital=_optional_endpoint(components.get("capital"), "capital"),
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


def _endpoint(value: object, component: str) -> ComponentEndpoint:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{component} endpoint must be an object")
    socket = value.get("socket")
    if not isinstance(socket, str) or not socket.strip():
        raise RuntimeError(f"{component} endpoint is missing socket")
    snapshot = value.get("snapshot")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot.strip()):
        raise RuntimeError(f"{component} endpoint has an invalid snapshot")
    view_root = value.get("view_root")
    if view_root is not None and (
        not isinstance(view_root, str) or not view_root.strip()
    ):
        raise RuntimeError(f"{component} endpoint has an invalid view_root")
    required_segments = value.get("required_segments", [])
    if not isinstance(required_segments, list) or any(
        not isinstance(segment, str) or not segment.strip()
        for segment in required_segments
    ):
        raise RuntimeError(f"{component} endpoint has invalid required_segments")
    broker = value.get("broker")
    if broker is not None and (not isinstance(broker, str) or not broker.strip()):
        raise RuntimeError(f"{component} endpoint has an invalid broker")
    lease_fence = value.get("lease_fence")
    if lease_fence is not None and (
        not isinstance(lease_fence, str) or not lease_fence.strip()
    ):
        raise RuntimeError(f"{component} endpoint has an invalid lease_fence")
    return ComponentEndpoint(
        component=component,
        socket=Path(socket),
        snapshot=None if snapshot is None else Path(snapshot),
        view_root=None if view_root is None else Path(view_root),
        required_segments=tuple(
            dict.fromkeys(segment.strip() for segment in required_segments)
        ),
        broker=None if broker is None else broker.strip(),
        lease_fence=None if lease_fence is None else lease_fence.strip(),
    )


def _optional_endpoint(value: object, component: str) -> ComponentEndpoint | None:
    return None if value is None else _endpoint(value, component)
