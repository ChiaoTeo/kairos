from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

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
from kairospy.surface.workbench.screens.flows import market, reference
from kairospy.surface.workbench.screens.flows.market.actions import (
    MarketFilePromptState,
    file_result_renderable,
    observation_renderable,
)
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    ChoiceInteraction,
    ConfirmInteraction,
    ControlInteraction,
    Feature,
    InputInteraction,
    WorkbenchCommandInput,
)
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static


from app_support import (
    log_text as _log_text,
    market as _market,
    workbench_state as _state,
)


def test_market_command_runs_in_worker_and_presents_result_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[str, str, int, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            actions = screen.query_one("#guided-actions", ActionList)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
                actions.option_count,
                str(actions._options[0].prompt),
            )

    output, status, option_count, choice = asyncio.run(run())

    assert output == ""
    assert "AAPL" in choice
    assert "nasdaq · equity · active" in choice
    assert "找到 1 个标的" not in output
    assert status == "找到 1 个结果 · 请选择"
    assert option_count == 1


def test_guided_market_search_records_intent_without_persisting_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[str, str, tuple[dict[str, object], ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            screen.submit("1")
            screen.submit("1")
            screen.submit("AAPL")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
                app.transcript.events,
            )

    output, status, events = asyncio.run(run())
    operation = "搜索市场标的 · AAPL"
    assert output == ""
    assert any(
        event["event"] == "action" and event.get("display") == operation
        for event in events
    )
    assert "kairos › 1" not in output
    assert "kairos › AAPL" not in output
    assert "找到 1 个标的" not in output
    assert status == "找到 1 个结果 · 请选择"


def test_reference_search_and_numbered_result_stay_in_one_input_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[type[object], str, int, bool, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

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
                str(screen.query_one("#command-status", Static).render()),
            )

    screen_type, context, option_count, input_focused, output, status = asyncio.run(
        run()
    )
    assert screen_type is CommandLineScreen
    assert context == "trader / 市场标的 / 查询结果  ›"
    assert option_count == 1
    assert input_focused
    assert "找到 1 条交易标的记录" not in output
    assert status == "找到 1 个结果 · 请选择"


def test_market_search_owns_action_area_until_results_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[str, bool, str, str, int, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            await pilot.press("1", "enter", "1", "enter")
            await pilot.pause()
            assert (
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder
                == "输入代码或名称"
            )
            prompt_context = str(screen.query_one("#command-context", Static).render())
            prompt_actions = screen.query_one("#guided-actions", ActionList)
            prompt_actions_visible = prompt_actions.display
            prompt_hints = str(screen.query_one("#command-hints", Static).render())

            await pilot.press("a", "a", "p", "l", "enter")
            await pilot.pause(0.1)
            return (
                prompt_context,
                prompt_actions_visible,
                prompt_hints,
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#guided-actions", ActionList).option_count,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    (
        prompt_context,
        prompt_actions_visible,
        prompt_hints,
        context,
        option_count,
        input_focused,
        output,
    ) = asyncio.run(run())
    assert prompt_context == "trader / 市场行情  ›"
    assert not prompt_actions_visible
    assert prompt_hints.splitlines() == [
        "Enter 搜索  ·  Esc 返回",
        "Alt+↑↓ 滚动  ·  PgUp/PgDn 翻页  ·  Ctrl+End 最新",
    ]
    assert context == "trader / 市场行情 / 查询结果  ›"
    assert option_count == 1
    assert input_focused
    assert "找到 1 个标的" not in output


def test_guided_market_observation_and_back_keep_one_screen_and_search_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[tuple[str, str]] = []
    monkeypatch.setattr(
        market,
        "load_routes",
        lambda state, market, observation: (
            {"provider": "first"},
            {"provider": "massive"},
        ),
    )

    def observation(
        state: object, market: object, observation_kind: str, provider: str
    ) -> dict[str, str]:
        observations.append((observation_kind, provider))
        if observation_kind == "bar":
            return {
                "symbol": "AAPL",
                "data_type": "bar",
                "provider": provider,
                "open": "225.00",
                "high": "228.00",
                "low": "224.50",
                "close": "227.50",
            }
        return {
            "symbol": "AAPL",
            "data_type": "quote",
            "provider": provider,
            "bid_price": "226.50",
            "ask_price": "226.75",
        }

    monkeypatch.setattr(
        market,
        "load_observation",
        observation,
    )
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[
        type[object], str, str, tuple[str, ...], ControlInteraction, bool
    ]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            for value in ("1", "1", "AAPL", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            assert screen.session.context == ("market", "providers")
            screen.submit("2")
            await pilot.pause(0.1)
            assert screen.session.context == ("market", "selected")
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            next_actions = tuple(action.id for action in interaction.actions)
            screen.submit("4")
            await pilot.pause(0.2)
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            screen.submit("/w")
            await pilot.pause(0.2)
            interaction = screen.session.interaction
            assert isinstance(interaction, ControlInteraction)
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause()
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                next_actions,
                interaction,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, next_actions, interaction, focused = asyncio.run(
        run()
    )
    with Console(width=100, record=True) as console:
        console.print(interaction.snapshot)
    control_text = console.export_text()

    assert screen_type is CommandLineScreen
    assert context == "trader / 市场行情 / 查询结果  ›"
    assert "226.50" in output
    assert "AAPL   QUOTE" in output
    assert "bar" in next_actions
    assert "trade" in next_actions
    assert "AAPL   BAR" in output
    assert "AAPL   BAR" in control_text
    assert ("bar", "massive") in observations
    assert "227.50" in control_text
    assert "massive" in control_text
    assert interaction.refreshing
    assert len(observations) >= 2
    assert focused


def test_single_market_route_appends_quote_to_activity_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))
    monkeypatch.setattr(
        market,
        "load_routes",
        lambda *args, **kwargs: ({"provider": "massive"},),
    )
    monkeypatch.setattr(
        market,
        "load_observation",
        lambda *args, **kwargs: {
            "symbol": "AAPL",
            "data_type": "quote",
            "provider": "massive",
            "bid_price": "226.50",
            "ask_price": "226.75",
            "observed_at_unix_nanos": 1_777_777_777_000_000_000,
            "_fetched_at_unix_nanos": 1_777_777_778_000_000_000,
            "_source_mode": "provider-direct",
            "_transport": "REST",
        },
    )

    async def run() -> tuple[str, str, tuple[Any, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("/market AAPL", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            output = screen.query_one("#command-output", RichLog)
            return (
                _log_text(output),
                str(screen.query_one("#command-status", Static).render()),
                output.activities,
            )

    output, status, activities = asyncio.run(run())

    assert len(activities) == 1
    assert "AAPL   QUOTE" in output
    assert "226.50" in output
    assert "226.75" in output
    assert "massive · REST · Provider 直连" in output
    assert "市场时间" in output
    assert "获取时间" in output
    assert "数据年龄  1.0 秒" in output
    assert "重新执行" in output
    command = activities[0].equivalent_command
    assert command is not None
    assert command[:2] == ("kairos", "market")
    assert "kairos-market-cli" not in command
    assert command[-4:] == (
        "--provider",
        "massive",
        "--observation-kind",
        "quote",
    )
    assert status == "行情已就绪"


def test_market_snapshot_is_only_appended_when_user_saves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))
    monkeypatch.setattr(
        market,
        "load_routes",
        lambda *args, **kwargs: ({"provider": "massive"},),
    )
    monkeypatch.setattr(
        market,
        "load_observation",
        lambda *args, **kwargs: {
            "symbol": "AAPL",
            "data_type": "quote",
            "provider": "massive",
            "bid_price": "226.50",
            "ask_price": "226.75",
        },
    )

    async def run() -> tuple[int, int, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("/market AAPL", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            output = screen.query_one("#command-output", RichLog)
            before_save = len(output.activities)
            screen.submit("/s")
            await pilot.pause()
            return before_save, len(output.activities), _log_text(output)

    before_save, after_save, output = asyncio.run(run())

    assert before_save == 1
    assert after_save == 2
    assert "保存行情快照" in output


def test_selecting_market_enters_named_context_without_printing_raw_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[str, str, tuple[str, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            for value in ("1", "1", "AAPL", "1"):
                screen.submit(value)
                await pilot.pause(0.1)

            actions = screen.query_one("#guided-actions", ActionList)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                tuple(str(option.prompt) for option in actions._options),
            )

    context, output, actions = asyncio.run(run())
    assert context == "trader / 市场行情 / 已选标的 · AAPL · nasdaq · equity  ›"
    assert "MarketId(" not in output
    assert "最新报价" in actions[0]
    assert "订单簿" in actions[1]


def test_order_book_observation_has_a_readable_two_sided_table() -> None:
    rendered = observation_renderable(
        {
            "symbol": "BTCUSDT",
            "data_type": "order_book",
            "provider": "binance",
            "bids": [["100", "2"]],
            "asks": [["101", "3"]],
        }
    )
    with Console(width=80, record=True) as console:
        console.print(rendered)
    output = console.export_text()
    assert "ORDER BOOK" in output
    assert "买价" in output
    assert "100" in output
    assert "101" in output


def test_workspace_snapshot_identifies_current_view_provenance() -> None:
    rendered = observation_renderable(
        {
            "symbol": "AAPL",
            "data_type": "quote",
            "provider": "massive",
            "bid_price": "309.18",
            "ask_price": "309.45",
            "source_observed_at_unix_nanos": 1_777_777_777_000_000_000,
            "_fetched_at_unix_nanos": 1_777_777_778_000_000_000,
            "_source_mode": "workspace-view",
        }
    )
    with Console(width=80, record=True) as console:
        console.print(rendered)
    output = console.export_text()

    assert "massive · Workspace 当前视图" in output
    assert "市场时间" in output
    assert "获取时间" in output


def test_market_replay_result_identifies_local_files_as_source() -> None:
    prompt = MarketFilePromptState("replay", _market())
    prompt.accept("files", "one.jsonl, two.jsonl")
    rendered = file_result_renderable({"status": "completed"}, prompt)
    with Console(width=80, record=True) as console:
        console.print(rendered)
    output = console.export_text()

    assert "来源" in output
    assert "本地回放" in output
    assert "2 个 JSONL 文件" in output


def test_market_history_download_is_a_single_input_redacted_scope_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[type[object], str, str, bool]:
        state = _state()
        state.dry_run = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
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
    assert context == "trader / 市场行情 / 已选标的 · AAPL · nasdaq · equity  ›"
    assert "Market 文件操作结果" in output
    assert "history/aapl.jsonl" in output
    assert "preview" in output
    assert focused


def test_market_replay_collects_multiple_files_and_confirms_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("1", "/r", "AAPL", "1", "one.jsonl, two.jsonl"):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, ConfirmInteraction)
            console = Console(width=100)
            with console.capture() as capture:
                console.print(interaction.summary)
            return (
                str(screen.query_one("#command-status", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                capture.get(),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    status, output, interaction, focused = asyncio.run(run())
    assert status == "等待确认"
    assert "one.jsonl" not in output
    assert "two.jsonl" not in output
    assert "one.jsonl" in interaction
    assert "two.jsonl" in interaction
    assert focused


def test_guided_reference_detail_technical_and_back_preserve_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = Asset(
        id="asset:usd",
        code="USD",
        name="US Dollar",
        asset_class="currency",
        status=ReferenceStatus.ACTIVE,
    )
    monkeypatch.setattr(reference, "load_records", lambda *args, **kwargs: (asset,))

    async def run() -> tuple[str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "USD", "1", "3"):
                screen.submit(value)
                await pilot.pause(0.05)
            selected = str(screen.query_one("#command-context", Static).render())
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause()
            results = str(screen.query_one("#command-context", Static).render())
            return (
                selected,
                results,
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    selected, results, output, focused = asyncio.run(run())
    assert selected == "trader / 市场标的 / 已选目录记录  ›"
    assert results == "trader / 市场标的 / 查询结果  ›"
    assert "asset:usd" in output
    assert focused


def test_guided_reference_instrument_type_is_an_explicit_input_step() -> None:
    async def run() -> tuple[object, str | None, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "3", "5"):
                screen.submit(value)
            interaction = screen.session.interaction
            assert isinstance(interaction, InputInteraction)
            return (
                interaction.action,
                screen.session.reference.instrument_type,
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    pending, instrument_type, placeholder, focused = asyncio.run(run())
    assert pending.feature is Feature.REFERENCE
    assert pending.action == "search"
    assert pending.field == "instruments"
    assert instrument_type == "option"
    assert placeholder == "输入代码或名称；直接回车浏览"
    assert focused
