from __future__ import annotations

import json
from io import StringIO

from kairospy.surface.cli import execute_argv
from kairospy.surface.console.models import (
    ObserveSnapshot,
    component_rows,
    launch_rows,
    recommended_action,
)
from kairospy.surface.console import ObserveApp
from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.launch.application import LaunchRegistryApplication


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
    rows = dict((row[0], row[1:]) for row in component_rows(snapshot))
    assert rows["market"][:2] == ("running", "1.2s ago")
    assert rows["account"][:2] == ("not_running", "-")


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

    assert launch_rows(snapshot)[0] == (
        "demo-backtest",
        "backtest",
        "completed",
        "two",
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
    assert "project, launch, runtime, and market" in output.getvalue()
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


def test_observe_app_refreshes_in_headless_textual_session() -> None:
    import asyncio

    class Reader:
        def read(self):
            return ObserveSnapshot("demo", {"market": {"status": "ready"}})

    async def run() -> str:
        async with ObserveApp(Reader(), refresh_seconds=60).run_test() as pilot:
            await pilot.pause(0.1)
            summary = str(pilot.app.query_one("#summary").render())
            status = str(pilot.app.query_one("#status").render())
            return summary + status

    assert "demo" in asyncio.run(run())
    assert "kairos project doctor" in asyncio.run(run())
