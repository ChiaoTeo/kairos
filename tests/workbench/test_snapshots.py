from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rich.text import Text

from kairospy.investment.apps.reference.application.models import (
    InstrumentRef,
    Market,
    MarketStatus,
)
from kairospy.primitives.reference import ExchangeId, InstrumentId, MarketId
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.screens.command_line import CommandLineScreen
from kairospy.surface.workbench.screens.navigation import Routes
from kairospy.surface.workbench.screens.selection import LaunchRecordView
from kairospy.surface.workbench.screens.activity import (
    ActivityKind,
    ActivityOutcome,
    ActivityRecord,
)
from kairospy.surface.workbench.screens.flows import market
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.widgets import ActionItem, InteractionHeading


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


def test_command_line_dark_100x30(snap_compare: Any) -> None:
    assert snap_compare(KairosWorkbenchApp(_state()), terminal_size=(100, 30))


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


def test_command_project_status(snap_compare: Any) -> None:
    async def show_project_status(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen.submit("7")
        await pilot.pause()
        screen.submit("1")
        await pilot.pause(0.1)

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(120, 36),
        run_before=show_project_status,
    )


def test_command_project_doctor(snap_compare: Any) -> None:
    async def show_project_doctor(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen.session.enter("project")
        screen._show_context()
        screen._start_operation(
            OperationSpec.create(
                action_name="operations.project.doctor",
                audit_summary="检查项目",
                route=ResultRoute(ResultKind.OPERATIONS_PROJECT),
                running_status="正在检查项目…",
                operation=lambda: {
                    "ok": False,
                    "ready": False,
                    "issues": [
                        "launch aapl-paper: Workspace data connection is unavailable: primary-live: 'provider connection does not exist: primary-live'",
                        "launch btc-paper: Workspace data connection is unavailable: primary-live: 'provider connection does not exist: primary-live'",
                        "launch aapl-paper: Account requires a successful manual connection test: paper-account",
                    ],
                    "missing_directories": [],
                    "launches": [
                        {"launch_id": "aapl-paper"},
                        {"launch_id": "btc-paper"},
                    ],
                },
            )
        )
        await pilot.pause(0.1)

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(120, 36),
        run_before=show_project_doctor,
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


def test_workspace_market_subscription_search_uses_business_copy(
    snap_compare: Any,
) -> None:
    async def request_subscription(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        for value in ("1", "2", "2"):
            screen.submit(value)
            await pilot.pause(0.05)
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(100, 30),
        run_before=request_subscription,
    )


def test_command_market_results(snap_compare: Any) -> None:
    async def show_results(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        spec = OperationSpec.create(
            action_name="market.find",
            audit_summary="搜索市场标的 · AAPL",
            route=ResultRoute(ResultKind.MARKET),
            operation=lambda: None,
            running_status="正在搜索市场标的…",
        )
        effects = market.handle_success(
            pilot.app.state, screen.session, spec, (_market(),)
        )
        assert effects is not None
        screen._apply_effects(effects)
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(100, 30),
        run_before=show_results,
    )


def test_command_confirmation_uses_interaction_region(snap_compare: Any) -> None:
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


def test_command_readiness_keeps_compact_task_context(snap_compare: Any) -> None:
    async def show_readiness(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen.session.strategy.selected_record = LaunchRecordView(
            {"launch_id": "paper-demo", "mode": "paper"}
        )
        screen.session.enter_context(Routes.STRATEGY_READINESS)
        screen.session.choose(
            (
                ActionItem("retry", "重新校验运行条件", "修复后重新读取全部条件", "1"),
                ActionItem("data", "修复行情连接", "配置并验证市场数据连接", "2"),
            ),
            heading=InteractionHeading("paper-demo", "paper · 运行条件"),
            state="需要处理",
        )
        screen._interaction().present(screen.session.interaction)
        screen._sync_context_chrome()
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(100, 30),
        run_before=show_readiness,
    )


def test_command_observe_degraded_state(snap_compare: Any) -> None:
    snapshot = ObserveSnapshot(
        workspace_id="visual-fixture",
        shared_services={
            "market": {"status": "degraded", "freshness": "stale"},
        },
        active_instances=(
            {
                "launch_id": "paper-demo",
                "mode": "paper",
                "state": "degraded",
                "instance_id": "instance-1",
            },
        ),
        observed_at=datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc),
    )

    async def show_snapshot(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        screen._read_observe = lambda: snapshot  # type: ignore[method-assign]
        screen.submit("/observe")
        await pilot.pause(0.1)

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(120, 36),
        run_before=show_snapshot,
    )


def test_reproducible_activity_stream_60x20(snap_compare: Any) -> None:
    async def show_activities(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        output = screen._output()
        output.append_activity(
            ActivityRecord(
                activity_id="quote-1",
                kind=ActivityKind.QUERY,
                outcome=ActivityOutcome.SUCCESS,
                title="AAPL · 最新报价 · massive",
                body=Text(
                    "买价  309.18    卖价  309.45\n来源  massive · REST · Provider 直连"
                ),
                equivalent_command=(
                    "kairos-market-cli",
                    "--workspace",
                    "/workspace/visual-fixture/.kairos",
                    "standalone",
                    "once",
                    "--symbol",
                    "AAPL",
                    "--provider",
                    "massive",
                    "--observation-kind",
                    "quote",
                ),
            )
        )
        output.append_activity(
            ActivityRecord(
                activity_id="book-1",
                kind=ActivityKind.QUERY,
                outcome=ActivityOutcome.FAILURE,
                title="AAPL · 订单簿 · 路由不可用",
                body=Text("请求  equity / order-book\n建议  检查 Market 数据连接"),
            )
        )
        screen.query_one("#activity-empty").display = False
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(60, 20),
        run_before=show_activities,
    )


def test_focused_activity_multiselect(snap_compare: Any) -> None:
    async def select_activities(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        output = screen._output()
        for index in range(1, 4):
            output.append_activity(
                ActivityRecord(
                    activity_id=f"selection-{index}",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title=f"Activity 结果 {index}",
                    body=Text(f"这是第 {index} 条可跨页选择和复制的内容。"),
                )
            )
        await pilot.pause()
        await pilot.press("tab", "tab", "home", "space", "down", "space")
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(100, 30),
        run_before=select_activities,
    )


def test_focused_activity_has_default_copy_target(snap_compare: Any) -> None:
    async def focus_latest_activity(pilot: Any) -> None:
        screen = pilot.app.screen
        assert isinstance(screen, CommandLineScreen)
        output = screen._output()
        for index in range(1, 4):
            output.append_activity(
                ActivityRecord(
                    activity_id=f"default-copy-{index}",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title=f"默认复制目标 {index}",
                    body=Text(f"这是第 {index} 条内容。"),
                )
            )
        await pilot.pause()
        await pilot.press("tab", "tab")
        await pilot.pause()

    assert snap_compare(
        KairosWorkbenchApp(_state()),
        terminal_size=(100, 30),
        run_before=focus_latest_activity,
    )
