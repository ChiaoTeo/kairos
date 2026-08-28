from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest

from kairospy.system.apps.components.application.event_routes import (
    ensure_instance_event_route,
    ensure_workspace_event_route,
    release_instance_event_route,
)
from kairospy.system.apps.launch.application.connections import (
    resolve_instance_connections,
)
from kairospy.system.apps.launch.application.runtime import write_instance_manifest
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_system_leases_stable_and_isolated_workspace_and_instance_routes(
    tmp_path: Path,
) -> None:
    first = WorkspaceApplication().init(tmp_path / "first", workspace_id="first")
    second = WorkspaceApplication().init(tmp_path / "second", workspace_id="second")
    one = first.instance("paper", "launch", "one")
    two = first.instance("paper", "launch", "two")

    workspace_route = ensure_workspace_event_route(first)
    one_route = ensure_instance_event_route(one)
    two_route = ensure_instance_event_route(two)
    other_workspace_route = ensure_workspace_event_route(second)

    assert ensure_workspace_event_route(first) == workspace_route
    assert ensure_instance_event_route(one) == one_route
    assert len(
        {
            workspace_route.channel,
            one_route.channel,
            two_route.channel,
            other_workspace_route.channel,
        }
    ) == 4
    assert workspace_route.scope == "workspace"
    assert one_route.scope == "instance"
    assert one_route.instance_id == "one"


def test_instance_route_is_released_only_by_system_cleanup(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="release")
    instance = workspace.instance("paper", "launch", "one")
    route = ensure_instance_event_route(instance)
    route_file = instance.paths.child("run", "transport", "instance-route.json")
    assert route_file.is_file()

    release_instance_event_route(instance)

    assert not route_file.exists()
    replacement = ensure_instance_event_route(instance)
    assert replacement.scope == "instance"
    assert replacement.workspace_id == route.workspace_id


def test_manifest_routes_are_typed_and_components_reference_scope(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="typed")
    instance = workspace.instance("paper", "launch", "one")
    workspace_route = ensure_workspace_event_route(workspace)
    instance_route = ensure_instance_event_route(instance)
    instance.prepare()
    instance.component_manifest().write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workspace_id": workspace.workspace_id,
                "launch_id": instance.launch_id,
                "instance_id": instance.instance_id,
                "mode": instance.mode,
                "event_routes": {
                    workspace_route.route_id: workspace_route.as_manifest(),
                    instance_route.route_id: instance_route.as_manifest(),
                },
                "components": {
                    "reference": {
                        "socket": str(workspace.paths.process_socket("reference")),
                        "event_route": workspace_route.route_id,
                    },
                    "market": {
                        "socket": str(instance.socket("market")),
                        "event_route": instance_route.route_id,
                    },
                    "risk": {
                        "socket": str(instance.socket("risk")),
                        "event_route": instance_route.route_id,
                    },
                },
                "accounts": {
                    "main": {
                        "socket": str(instance.socket("account-main")),
                        "event_route": instance_route.route_id,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    connections = resolve_instance_connections(instance)

    assert connections.reference is not None
    assert connections.reference.event_route == workspace_route
    assert connections.market is not None
    assert connections.market.event_route == instance_route
    assert connections.accounts[next(iter(connections.accounts))].event_route == instance_route


def test_manifest_rejects_reference_on_instance_route(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="wrong")
    instance = workspace.instance("paper", "launch", "one")
    route = ensure_instance_event_route(instance)
    instance.prepare()
    instance.component_manifest().write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workspace_id": workspace.workspace_id,
                "launch_id": instance.launch_id,
                "instance_id": instance.instance_id,
                "mode": instance.mode,
                "event_routes": {route.route_id: route.as_manifest()},
                "components": {
                    "reference": {
                        "socket": str(workspace.paths.process_socket("reference")),
                        "event_route": route.route_id,
                    }
                },
                "accounts": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reference must use"):
        resolve_instance_connections(instance)


def test_two_instances_share_workspace_market_but_not_instance_owners(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="shared")
    instances = (
        workspace.instance("paper", "launch", "one"),
        workspace.instance("paper", "launch", "two"),
    )
    workspace_route = ensure_workspace_event_route(workspace)
    resolved = []
    for instance in instances:
        instance.prepare()
        instance_route = ensure_instance_event_route(instance)
        write_instance_manifest(
            instance,
            accounts={},
            components={
                "market": {
                    "socket": str(workspace.paths.process_socket("market")),
                    "event_route": workspace_route.route_id,
                },
                "risk": {
                    "socket": str(instance.socket("risk")),
                    "event_route": instance_route.route_id,
                },
            },
            event_routes={
                workspace_route.route_id: workspace_route,
                instance_route.route_id: instance_route,
            },
        )
        resolved.append(resolve_instance_connections(instance))

    assert resolved[0].market is not None and resolved[1].market is not None
    assert resolved[0].market.event_route == resolved[1].market.event_route
    assert resolved[0].risk is not None and resolved[1].risk is not None
    assert resolved[0].risk.event_route != resolved[1].risk.event_route


def test_persisted_route_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="owner")
    instance = workspace.instance("paper", "launch", "one")
    route = ensure_instance_event_route(instance)
    route_file = instance.paths.child("run", "transport", "instance-route.json")
    value = route.as_manifest()
    value["instance_id"] = "other"
    route_file.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not belong"):
        ensure_instance_event_route(instance)


def test_missing_route_is_not_reallocated_while_owner_lock_is_held(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="active")
    instance = workspace.instance("paper", "launch", "one")
    ensure_instance_event_route(instance)
    route_file = instance.paths.child("run", "transport", "instance-route.json")
    process_lock = instance.paths.process_lock("risk")
    process_lock.parent.mkdir(parents=True, exist_ok=True)

    with process_lock.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        route_file.unlink()
        with pytest.raises(RuntimeError, match="owner runtime is active"):
            ensure_instance_event_route(instance)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    replacement = ensure_instance_event_route(instance)
    assert replacement.instance_id == "one"
