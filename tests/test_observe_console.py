from __future__ import annotations

import json
from io import StringIO

from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.observe_rendering import recommended_action
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
        components={
            "reference": {"status": "ready", "pid": 10},
            "market": {"status": "running", "last_event_age_ms": 1250},
            "account": {"status": "not_running"},
        },
    )

    assert snapshot.overall_status == "partial"
    rendered = renderable_plain_text(observe_renderable(snapshot))
    assert "market" in rendered and "running" in rendered and "1.2s ago" in rendered
    assert "account" in rendered and "not_running" in rendered


def test_observe_snapshot_presents_latest_launch_first() -> None:
    snapshot = ObserveSnapshot(
        workspace_id="demo",
        components={},
        launches=(
            {
                "launch_id": "older",
                "mode": "paper",
                "state": "stopped",
                "instance_id": "one",
                "updated_at": "2026-08-10T01:00:00+00:00",
            },
            {
                "launch_id": "demo-backtest",
                "mode": "backtest",
                "state": "completed",
                "instance_id": "two",
                "updated_at": "2026-08-10T02:00:00+00:00",
            },
        ),
    )

    assert recommended_action(snapshot) == "kairos launch report demo-backtest"


def test_observe_recommends_logs_for_a_failed_launch() -> None:
    snapshot = ObserveSnapshot(
        workspace_id="demo",
        components={"market": {"status": "ready"}},
        launches=(
            {
                "launch_id": "demo",
                "mode": "backtest",
                "state": "failed",
                "instance_id": "one",
            },
        ),
    )

    assert snapshot.overall_status == "degraded"
    assert recommended_action(snapshot) == "kairos launch logs demo"


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
    output = StringIO()

    assert (
        execute_argv(
            ["observe", "--once", "--workspace", str(tmp_path / "demo")], output
        )
        == 0
    )

    value = json.loads(output.getvalue())
    assert value["workspace_id"] == "demo"
    assert value["components"]["market"]["status"] == "not_running"
    assert value["launches"][0]["launch_id"] == "demo-backtest"
    assert value["launches"][0]["state"] == "completed"
    assert value["next_action"] == "kairos launch report demo-backtest"
    assert value["market_snapshot"] is None
