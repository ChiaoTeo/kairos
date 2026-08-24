from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest

from kairospy.strategy.apps.agent.application.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.investment.apps.reference.application.models import (
    Asset,
    InstrumentRef,
    Market,
    MarketStatus,
    ReferenceStatus,
)
from kairospy.primitives.reference import ExchangeId, InstrumentId, MarketId
from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.screens.command_line import CommandLineScreen
from kairospy.surface.workbench.screens.guided.strategy import LaunchWizardState
from kairospy.surface.console.models import ObserveSnapshot
from kairospy.surface.workbench.widgets import ActionList, WorkbenchCommandInput
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static


from app_support import (
    log_text as _log_text,
    market as _market,
    workbench_state as _state,
)


def test_market_command_runs_in_worker_and_renders_result() -> None:
    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = lambda query: (_market(),)  # type: ignore[method-assign]
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
            )

    output, status = asyncio.run(run())

    assert "找到 1 个标的" in output
    assert "AAPL" in output
    assert status == "首页 / 市场行情 / 查询结果 · 请选择结果"


def test_reference_search_and_numbered_result_stay_in_one_input_stream() -> None:
    async def run() -> tuple[type[object], str, int, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_reference_records = (  # type: ignore[method-assign]
                lambda kind, query: (_market(),)
            )

            await pilot.press("2", "enter", "4", "enter")
            await pilot.pause()
            assert (
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder
                == "输入代码或名称；直接回车浏览"
            )

            await pilot.press("a", "a", "p", "l", "enter")
            await pilot.pause(0.1)
            actions = screen.query_one("#guided-actions", ActionList)
            context = str(screen.query_one("#command-context", Static).render())
            output = _log_text(screen.query_one("#command-output", RichLog))
            return (
                type(app.screen),
                context,
                actions.option_count,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
                output,
            )

    screen_type, context, option_count, input_focused, output = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 市场标的 / 查询结果  ›"
    assert option_count == 1
    assert input_focused
    assert "找到 1 条交易标的记录" in output


def test_market_menu_search_keeps_bottom_input_and_numbered_results() -> None:
    async def run() -> tuple[str, int, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = lambda query: (_market(),)  # type: ignore[method-assign]

            await pilot.press("1", "enter", "1", "enter")
            await pilot.pause()
            assert (
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder
                == "请输入市场代码、名称或完整 Market ID"
            )

            await pilot.press("a", "a", "p", "l", "enter")
            await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#guided-actions", ActionList).option_count,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    context, option_count, input_focused, output = asyncio.run(run())
    assert context == "首页 / 市场行情 / 查询结果  ›"
    assert option_count == 1
    assert input_focused
    assert "找到 1 个标的" in output


def test_guided_market_observation_and_back_keep_one_screen_and_search_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.load_market_routes",
        lambda state, market, observation: (
            {"provider": "first"},
            {"provider": "massive"},
        ),
    )
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.load_market_observation",
        lambda state, market, observation, provider: {
            "symbol": "AAPL",
            "data_type": "quote",
            "provider": provider,
            "bid_price": "226.50",
            "ask_price": "226.75",
        },
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = lambda query: (_market(),)  # type: ignore[method-assign]

            for value in ("1", "1", "AAPL", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            assert screen.session.context == ("market", "providers")
            screen.submit("2")
            await pilot.pause(0.1)
            assert screen.session.context == ("market", "selected")
            screen.submit("/back")
            await pilot.pause()
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 市场行情 / 查询结果  ›"
    assert "226.50" in output
    assert "massive" in output
    assert focused


def test_market_history_download_is_a_single_input_redacted_scope_preview() -> None:
    async def run() -> tuple[type[object], str, str, bool]:
        state = _state()
        state.dry_run = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = lambda query: (_market(),)  # type: ignore[method-assign]
            for value in ("1", "2", "AAPL", "1", "", "", "", "history/aapl.jsonl"):
                screen.submit(value)
                await pilot.pause(0.03)
            await pilot.pause(0.1)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 市场行情 / 已选标的  ›"
    assert "Market 文件操作范围" in output
    assert "history/aapl.jsonl" in output
    assert "preview" in output
    assert focused


def test_market_replay_collects_multiple_files_and_confirms_inline() -> None:
    async def run() -> tuple[str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = lambda query: (_market(),)  # type: ignore[method-assign]
            for value in ("1", "/r", "AAPL", "1", "one.jsonl, two.jsonl"):
                screen.submit(value)
                await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-status", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    status, output, focused = asyncio.run(run())
    assert status == "等待确认"
    assert "one.jsonl" in output
    assert "two.jsonl" in output
    assert "/confirm" in output
    assert focused


def test_guided_reference_detail_technical_and_back_preserve_results() -> None:
    asset = Asset(
        id="asset:usd",
        code="USD",
        name="US Dollar",
        asset_class="currency",
        status=ReferenceStatus.ACTIVE,
    )

    async def run() -> tuple[str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_reference_records = (  # type: ignore[method-assign]
                lambda kind, query: (asset,)
            )
            for value in ("2", "1", "USD", "1", "3"):
                screen.submit(value)
                await pilot.pause(0.05)
            selected = str(screen.query_one("#command-context", Static).render())
            screen.submit("/back")
            await pilot.pause()
            results = str(screen.query_one("#command-context", Static).render())
            return (
                selected,
                results,
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    selected, results, output, focused = asyncio.run(run())
    assert selected == "首页 / 市场标的 / 已选目录记录  ›"
    assert results == "首页 / 市场标的 / 查询结果  ›"
    assert "asset:usd" in output
    assert focused


def test_guided_reference_instrument_type_is_an_explicit_input_step() -> None:
    async def run() -> tuple[str, str | None, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "3", "5"):
                screen.submit(value)
            return (
                screen.session.pending_action or "",
                screen.session.reference_instrument_type,
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    pending, instrument_type, placeholder, focused = asyncio.run(run())
    assert pending == "reference:instruments"
    assert instrument_type == "option"
    assert placeholder == "输入代码或名称；直接回车浏览"
    assert focused
