from __future__ import annotations

import json
from io import StringIO

from kairospy.surface.cli import execute_argv
from kairospy.surface.workbench.screens.flows.operations.observe_view import (
    observe_renderable,
)
from kairospy.surface.workbench.widgets import renderable_plain_text
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.system.apps.launch.application import LaunchRegistryApplication


def test_observe_snapshot_aggregates_component_health_and_freshness() -> None:
    snapshot = ObserveSnapshot(
        workspace_id="demo",
        shared_services={
            "reference": {"status": "not_running", "pid": 10},
            "market": {"status": "running", "last_event_age_ms": 1250},
        },
    )

    assert snapshot.overall_status == "partial"
    rendered = renderable_plain_text(observe_renderable(snapshot))
    assert "market" in rendered and "running" in rendered and "1.2s ago" in rendered
    assert "reference" in rendered and "not_running" in rendered


def test_observe_snapshot_tracks_only_current_active_instances() -> None:
    snapshot = ObserveSnapshot(
        workspace_id="demo",
        shared_services={},
        active_instances=(
            {
                "launch_id": "older",
                "mode": "paper",
                "state": "running",
                "instance_id": "one",
                "updated_at": "2026-08-10T01:00:00+00:00",
            },
            {
                "launch_id": "demo-paper",
                "mode": "paper",
                "state": "running",
                "instance_id": "two",
                "updated_at": "2026-08-10T02:00:00+00:00",
            },
        ),
    )

    assert [value["instance_id"] for value in snapshot.active_instances] == [
        "one",
        "two",
    ]


def test_observe_current_health_includes_a_degraded_active_instance() -> None:
    snapshot = ObserveSnapshot(
        workspace_id="demo",
        shared_services={"market": {"status": "ready"}},
        active_instances=(
            {
                "launch_id": "demo",
                "mode": "backtest",
                "state": "degraded",
                "instance_id": "one",
            },
        ),
    )

    assert snapshot.overall_status == "degraded"


def test_observe_command_is_registered() -> None:
    output = StringIO()

    assert execute_argv(["observe", "--help"], output) == 0
    assert "Usage: kairospy observe" in output.getvalue()
    assert "--once" in output.getvalue()


def test_observe_once_emits_machine_readable_component_inventory(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    LaunchRegistryApplication(workspace).add(
        "demo-backtest", mode="backtest", instance_id="run-1"
    )
    LaunchRegistryApplication(workspace).update_state(
        "demo-backtest", mode="backtest", instance_id="run-1", state="completed"
    )
    LaunchRegistryApplication(workspace).add(
        "demo-paper", mode="paper", instance_id="run-2"
    )
    LaunchRegistryApplication(workspace).update_state(
        "demo-paper", mode="paper", instance_id="run-2", state="running"
    )
    output = StringIO()

    assert (
        execute_argv(
            ["observe", "--once", "--workspace", str(tmp_path / "demo")], output
        )
        == 0
    )

    value = json.loads(output.getvalue())
    assert value["workspace_id"] == "demo"
    assert value["shared_services"]["market"]["status"] == "not_running"
    assert [item["launch_id"] for item in value["active_instances"]] == [
        "demo-paper"
    ]
    assert value["overall_status"] == "partial"
    assert "next_action" not in value
    assert "market_snapshot" not in value
