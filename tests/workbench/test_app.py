from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from kairospy.application.reference.models import (
    Asset,
    InstrumentRef,
    Market,
    MarketStatus,
    ReferenceStatus,
)
from kairospy.primitives.reference import ExchangeId, InstrumentId, MarketId
from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.screens.market import MarketDetailScreen, MarketScreen
from kairospy.surface.workbench.screens.launch_setup import LaunchSetupScreen
from kairospy.surface.workbench.screens.operations import (
    AdvancedConfigScreen,
    OperationsScreen,
    ProjectScreen,
)
from kairospy.surface.workbench.screens.reference import (
    ReferenceDetailScreen,
    ReferenceScreen,
)
from kairospy.surface.workbench.screens.resource_setup import ResourceSetupScreen
from kairospy.surface.workbench.screens.research import ResearchScreen
from kairospy.surface.workbench.screens.resources import ResourcesScreen
from kairospy.surface.workbench.screens.strategy import StrategyScreen
from kairospy.surface.workbench.widgets import ActionList
from textual.containers import Vertical
from textual.widgets import DataTable, Input, Label, Select


def _state() -> WorkbenchState:
    owner = SimpleNamespace(
        workspace_id="trader",
        paths=SimpleNamespace(
            root=Path("/workspace/trader/.kairos"),
            project_root=Path("/workspace/trader"),
        ),
    )
    return WorkbenchState(owner=owner, workspace_arg=owner.paths.root)


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


def test_home_is_one_textual_screen_with_six_product_actions() -> None:
    async def run() -> tuple[int, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            actions = app.screen.query_one("#home-actions", ActionList)
            workspace = str(app.screen.query_one("#workspace-summary").render())
            return actions.option_count, app.screen.sub_title or "", workspace

    count, subtitle, workspace = asyncio.run(run())

    assert count == 6
    assert subtitle == "首页"
    assert "trader" in workspace


def test_workspace_identity_is_visible_in_shared_header_context() -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return app.screen.title or ""

    assert asyncio.run(run()) == "Kairos · trader"


def test_home_numeric_shortcut_dispatches_selected_product() -> None:
    async def run() -> list[str]:
        app = KairosWorkbenchApp(_state())
        opened: list[str] = []
        app.open_section = opened.append  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("1")
            await pilot.pause()
        return opened

    assert asyncio.run(run()) == ["market"]


def test_home_layout_runs_at_supported_terminal_sizes() -> None:
    async def run(size: tuple[int, int]) -> int:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return app.screen.query_one("#home-actions", ActionList).option_count

    for size in ((60, 20), (80, 24), (120, 30), (160, 40)):
        assert asyncio.run(run(size)) == 6


def test_market_search_stays_inside_screen_and_opens_selected_market() -> None:
    async def run() -> tuple[int, str]:
        app = KairosWorkbenchApp(_state())
        original = MarketScreen._find_markets
        MarketScreen._find_markets = lambda self, query: (_market(),)  # type: ignore[method-assign]
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("1")
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, MarketScreen)
                screen.action_search()
                search = screen.query_one("#market-search", Input)
                search.value = "AAPL"
                search.focus()
                await pilot.press("enter")
                await pilot.pause(0.1)
                table = screen.query_one("#market-results", DataTable)
                assert table.row_count == 1
                table.focus()
                await pilot.press("enter")
                await pilot.pause()
                return table.row_count, app.screen.sub_title or ""
        finally:
            MarketScreen._find_markets = original

    rows, subtitle = asyncio.run(run())

    assert rows == 1
    assert subtitle == "首页 › 市场行情 › AAPL"


def test_market_observation_uses_application_result_in_current_screen() -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        original_routes = MarketDetailScreen._load_routes
        original_observation = MarketDetailScreen._load_observation
        MarketDetailScreen._load_routes = (  # type: ignore[method-assign]
            lambda self, kind: ({"provider": "demo"},)
        )
        MarketDetailScreen._load_observation = (  # type: ignore[method-assign]
            lambda self, provider: {"symbol": "AAPL", "price": "226.50"}
        )
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(MarketDetailScreen(_market()))
                await pilot.pause()
                actions = app.screen.query_one("#observation-actions", ActionList)
                actions.focus()
                await pilot.press("enter")
                await pilot.pause(0.2)
                return str(app.screen.query_one("#observation-status", Label).render())
        finally:
            MarketDetailScreen._load_routes = original_routes
            MarketDetailScreen._load_observation = original_observation

    assert asyncio.run(run()) == "行情已更新"


def test_reference_asset_search_and_detail_stay_in_screen_stack() -> None:
    asset = Asset(
        id="asset:usd",
        code="USD",
        name="US Dollar",
        asset_class="currency",
        status=ReferenceStatus.ACTIVE,
    )

    async def run() -> tuple[int, str, str]:
        app = KairosWorkbenchApp(_state())
        original = ReferenceScreen._find_records
        ReferenceScreen._find_records = lambda self, query: (asset,)  # type: ignore[method-assign]
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("2")
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, ReferenceScreen)
                screen._open_search("assets")
                search = screen.query_one("#reference-search", Input)
                search.value = "USD"
                search.focus()
                await pilot.press("enter")
                await pilot.pause(0.1)
                table = screen.query_one("#reference-results", DataTable)
                table.focus()
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, ReferenceDetailScreen)
                return (
                    table.row_count,
                    app.screen.sub_title or "",
                    str(app.screen.query_one("#reference-detail").render()),
                )
        finally:
            ReferenceScreen._find_records = original

    rows, subtitle, detail = asyncio.run(run())

    assert rows == 1
    assert subtitle == "首页 › 市场标的 › USD"
    assert "asset:usd" in detail


def test_escape_returns_to_preserved_reference_results_and_focus() -> None:
    asset = Asset(
        id="asset:usd",
        code="USD",
        name="US Dollar",
        asset_class="currency",
        status=ReferenceStatus.ACTIVE,
    )

    async def run() -> tuple[bool, int, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ReferenceScreen()
            app.push_screen(screen)
            await pilot.pause()
            screen._open_search("assets")
            screen._show_results((asset,))
            table = screen.query_one("#reference-results", DataTable)
            table.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return app.screen is screen, table.row_count, table.has_focus

    same_screen, rows, focused = asyncio.run(run())

    assert same_screen
    assert rows == 1
    assert focused


def test_all_home_products_open_real_screens_without_placeholder_routes() -> None:
    async def run(section: str) -> type[object]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.open_section(section)
            await pilot.pause()
            return type(app.screen)

    expected = {
        "market": MarketScreen,
        "reference": ReferenceScreen,
        "strategy": StrategyScreen,
        "resources": ResourcesScreen,
        "research": ResearchScreen,
        "operations": OperationsScreen,
    }
    for section, screen_type in expected.items():
        assert asyncio.run(run(section)) is screen_type


def test_text_input_consumes_global_shortcuts_as_text() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.open_section("market")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, MarketScreen)
            screen.action_search()
            search = screen.query_one("#market-search", Input)
            search.focus()
            await pilot.press("q", "1", "?")
            await pilot.pause()
            return search.value, app.is_running

    value, running = asyncio.run(run())

    assert value == "q1?"
    assert running


def test_resource_setup_keeps_secret_input_masked_and_escape_cancels() -> None:
    async def run() -> tuple[bool, bool]:
        app = KairosWorkbenchApp(_state())
        result: list[dict[str, object] | None] = []
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(ResourceSetupScreen("data"), result.append)
            await pilot.pause()
            secret = app.screen.query_one("#secret-primary", Input)
            masked = secret.password
            await pilot.press("escape")
            await pilot.pause()
            return masked, result == [None]

    masked, cancelled = asyncio.run(run())

    assert masked
    assert cancelled


def test_operations_routes_project_and_advanced_config_inside_screen_stack() -> None:
    async def run(shortcut: str) -> type[object]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(OperationsScreen())
            await pilot.pause()
            actions = app.screen.query_one("#operations-actions", ActionList)
            assert actions.highlight_shortcut(shortcut)
            actions.action_select()
            await pilot.pause()
            return type(app.screen)

    assert asyncio.run(run("1")) is ProjectScreen
    assert asyncio.run(run("6")) is AdvancedConfigScreen


def test_launch_setup_is_native_form_with_mode_context_and_cancel() -> None:
    async def run() -> tuple[bool, bool, bool]:
        app = KairosWorkbenchApp(_state())
        result: list[dict[str, object] | None] = []
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(LaunchSetupScreen("new-launch"), result.append)
            await pilot.pause()
            mode = app.screen.query_one("#launch-mode", Select)
            initial_backtest = app.screen.query_one(
                "#backtest-fields", Vertical
            ).display
            mode.value = "live"
            await pilot.pause()
            live_visible = app.screen.query_one("#live-fields", Vertical).display
            backtest_hidden = not app.screen.query_one(
                "#backtest-fields", Vertical
            ).display
            await pilot.press("escape")
            await pilot.pause()
            return initial_backtest, live_visible and backtest_hidden, result == [None]

    initial_backtest, switched_to_live, cancelled = asyncio.run(run())

    assert initial_backtest is False
    assert switched_to_live
    assert cancelled
