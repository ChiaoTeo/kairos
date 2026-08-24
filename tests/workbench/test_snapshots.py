from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kairospy.investment.apps.reference.application.models import (
    InstrumentRef,
    Market,
    MarketStatus,
)
from kairospy.primitives.reference import ExchangeId, InstrumentId, MarketId
from kairospy.surface.console.models import ObserveSnapshot
from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.screens.command_line import CommandLineScreen


def _state() -> WorkbenchState:
    owner = SimpleNamespace(
        workspace_id="visual-fixture",
        paths=SimpleNamespace(
            root=Path("/workspace/visual-fixture/.kairos"),
            project_root=Path("/workspace/visual-fixture"),
        ),
    )
    return WorkbenchState(
        owner=owner,
        workspace_arg=owner.paths.root,
        dry_run=True,
        no_exec=True,
    )


def _market() -> Market:
    return Market(
        id=MarketId("market:aapl-nasdaq"),
        instrument=InstrumentRef(InstrumentId("instrument:aapl"), "AAPL"),
        listing_id=None,
        exchange_id=ExchangeId("exchange:nasdaq"),
        instrument_kind="equity",
        venue_symbol="AAPL",
        quote_asset="USD",
        status=MarketStatus.ACTIVE,
    )


def test_command_line_dark_80x24(snap_compare: Any) -> None:
    assert snap_compare(KairosWorkbenchApp(_state()), terminal_size=(80, 24))


def test_command_line_dark_60x20(snap_compare: Any) -> None:
    assert snap_compare(KairosWorkbenchApp(_state()), terminal_size=(60, 20))


def test_command_help(snap_compare: Any) -> None:
    async def show_help(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen.submit("/help")
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(80, 24),
        run_before=show_help,
    )


def test_command_market_argument_guide(snap_compare: Any) -> None:
    async def request_query(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen.submit("/market")
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(80, 24),
        run_before=request_query,
    )


def test_command_market_results(snap_compare: Any) -> None:
    async def show_results(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen._record_action("market.find", ("AAPL",))
        screen._emit_operation("搜索市场标的")
        screen._render_result("market", (_market(),))
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(100, 30),
        run_before=show_results,
    )


def test_command_confirmation_stays_in_output_stream(snap_compare: Any) -> None:
    async def request_confirmation(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen.request_confirmation("启动实盘策略", lambda: None)
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(80, 24),
        run_before=request_confirmation,
    )


def test_command_observe_degraded_state(snap_compare: Any) -> None:
    snapshot = ObserveSnapshot(
        workspace_id="visual-fixture",
        components={
            "market": {"status": "degraded", "freshness": "stale"},
            "execution": {"status": "ready", "freshness": "current"},
        },
        launches=(
            {
                "launch_id": "paper-demo",
                "mode": "paper",
                "status": "degraded",
                "instance_id": "instance-1",
            },
        ),
        market_snapshot={"status": "stale", "generation": 7},
        observed_at=datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc),
    )

    async def show_snapshot(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen._record_action(
            "system.observe",
            (),
            equivalent_command=("kairos", "observe", "--once"),
        )
        screen._emit_operation("刷新系统状态")
        screen._render_result("observe", snapshot)
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(120, 36),
        run_before=show_snapshot,
    )
