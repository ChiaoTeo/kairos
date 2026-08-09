from __future__ import annotations

import json
from io import StringIO

from kairospy.surface.cli import execute_argv
from kairospy.surface.console.models import ObserveSnapshot, component_rows
from kairospy.surface.console import ObserveApp
from kairospy.application.workspace import WorkspaceApplication


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


def test_observe_command_is_registered() -> None:
    output = StringIO()

    assert execute_argv(["observe", "--help"], output) == 0
    assert "read-only system and market observation console" in output.getvalue()
    assert "--once" in output.getvalue()


def test_observe_once_emits_machine_readable_component_inventory(tmp_path) -> None:
    WorkspaceApplication().init_project(tmp_path / "demo", workspace_id="demo")
    output = StringIO()

    assert execute_argv(
        ["observe", "--once", "--workspace", str(tmp_path / "demo")], output
    ) == 0

    value = json.loads(output.getvalue())
    assert value["workspace_id"] == "demo"
    assert value["components"]["market"]["status"] == "not_running"
    assert value["market_snapshot"] is None


def test_observe_app_refreshes_in_headless_textual_session() -> None:
    import asyncio

    class Reader:
        def read(self):
            return ObserveSnapshot("demo", {"market": {"status": "ready"}})

    async def run() -> str:
        async with ObserveApp(Reader(), refresh_seconds=60).run_test() as pilot:
            await pilot.pause(0.1)
            return str(pilot.app.query_one("#summary").render())

    assert "demo" in asyncio.run(run())
