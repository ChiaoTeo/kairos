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

from kairospy.contracts.reference import ReferenceRuntimeStatusResponse
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
from kairospy.primitives.reference import AssetId, ExchangeId, InstrumentId, MarketId
from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.screens.command_line import CommandLineScreen
from kairospy.surface.workbench.screens.effects import RunOperation, SetInteraction
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.navigation import context_items
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.surface.workbench.screens.flows import market, reference
from kairospy.surface.workbench.screens.flows.operations import (
    actions as operations_actions,
)
from kairospy.surface.workbench.screens.flows.reference import (
    actions as reference_actions,
)
from kairospy.surface.workbench.screens.flows.reference.actions import (
    runtime_status_renderable,
)
from kairospy.surface.workbench.screens.flows.market.workspace import (
    routes_renderable as workspace_routes_renderable,
    status_renderable as workspace_status_renderable,
)
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
    interaction_copy_text,
)
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static


from app_support import (
    log_text as _log_text,
    market as _market,
    workbench_state as _state,
)


def test_market_letter_shortcut_is_dispatched_before_bare_cli_input() -> None:
    async def run() -> tuple[tuple[str, ...], str, int]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            screen.submit("1")
            screen.submit("2")
            await pilot.pause()
            return (
                screen.session.context,
                interaction_copy_text(screen.session.interaction),
                len(screen._output().activities),
            )

    context, interaction, activity_count = asyncio.run(run())

    assert context == ("market", "live-unavailable")
    assert "启动实时行情" in interaction
    assert "查看服务详细状态" in interaction
    assert activity_count == 0


def test_unavailable_live_market_offers_scoped_recovery_and_canonical_details() -> None:
    async def run(action: str) -> tuple[tuple[str, ...], object, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("1")
            screen.submit("2")
            screen.submit(action)
            await pilot.pause()
            return (
                screen.session.context,
                screen.session.interaction,
                interaction_copy_text(screen.session.interaction),
            )

    confirm_context, confirm, confirm_copy = asyncio.run(run("1"))
    assert confirm_context == ("market", "live-unavailable")
    assert isinstance(confirm, ConfirmInteraction)
    assert "项目共享 Market" in confirm.operation.audit_summary
    assert "项目共享行情服务" in confirm_copy

    detail_context, detail, detail_copy = asyncio.run(run("2"))
    assert detail_context == ("operations", "service", "market")
    assert isinstance(detail, ChoiceInteraction)
    assert "启动并保持运行" in detail_copy


def test_workspace_market_subscription_resolves_symbol_without_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[str, str, str]:
        state = _state()
        assert state.snapshot is not None
        state.snapshot.shared_services["market"] = {
            "status": "ready",
            "control_reachable": True,
            "pid_alive": True,
        }
        state.dry_run = True
        state.no_exec = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("1", "2", "2", "AAPL", "1"):
                screen.submit(value)
                await pilot.pause(0.05)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                interaction_copy_text(screen.session.interaction),
            )

    context, output, interaction = asyncio.run(run())

    assert context == "trader › 市场与标的 › 我的实时行情"
    assert "添加实时行情预演完成，未执行任何修改" in output
    assert "market:aapl-nasdaq" not in interaction
    assert "Market ID" not in interaction


def test_unsupported_explicit_kairos_command_is_rejected_before_dry_run() -> None:
    async def run() -> tuple[str, int]:
        state = _state()
        state.dry_run = True
        state.no_exec = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            screen.submit("kairos c")
            await pilot.pause()
            return (
                interaction_copy_text(screen.session.interaction),
                len(screen._output().activities),
            )

    interaction, activity_count = asyncio.run(run())

    assert "kairos c 尚未接入单输入 Application 分派" in interaction
    assert "请选择当前菜单中的操作" in interaction
    assert "重新执行" not in interaction
    assert activity_count == 0


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
    assert "AAPL · 股票" in choice
    assert "Nasdaq · AAPL · 当前有效" in choice
    assert "找到 1 个标的" not in output
    assert status == "找到 1 个结果 · 请选择"
    assert option_count == 1


def test_market_search_uses_reference_owned_v3_join_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _market()
    evidence = SimpleNamespace(conclusion="known")

    class Catalog:
        def find_venue_markets(self, **filters):
            assert filters["query"] == "AAPL"
            return SimpleNamespace(
                markets=(expected,),
                evidence=evidence,
            )

    monkeypatch.setattr(reference_actions, "_application", lambda _state: Catalog())

    records = reference_actions.load_records(object(), "markets", "AAPL")

    assert tuple(records) == (expected,)
    assert records.evidence is evidence


def test_market_search_groups_one_product_across_exchange_markets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markets = (
        Market(
            id=MarketId("market:binance:spot:BTCUSDT"),
            instrument=InstrumentRef(
                InstrumentId("instrument:binance:spot:BTCUSDT"), "BTC/USDT"
            ),
            listing_id=None,
            exchange_id=ExchangeId("exchange:binance"),
            instrument_kind="spot",
            venue_symbol="BTCUSDT",
            base_asset=AssetId("asset:BTC"),
            quote_asset=AssetId("asset:USDT"),
            status=MarketStatus.ACTIVE,
        ),
        Market(
            id=MarketId("market:okx:spot:BTC-USDT"),
            instrument=InstrumentRef(
                InstrumentId("instrument:okx:spot:BTC-USDT"), "BTC/USDT"
            ),
            listing_id=None,
            exchange_id=ExchangeId("exchange:okx"),
            instrument_kind="spot",
            venue_symbol="BTC-USDT",
            base_asset=AssetId("asset:BTC"),
            quote_asset=AssetId("asset:USDT"),
            status=MarketStatus.ACTIVE,
        ),
    )
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: markets)

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market BTCUSDT")
            await pilot.pause(0.1)
            return interaction_copy_text(screen.session.interaction)

    result = asyncio.run(run())
    assert "BTC/USDT · 现货" in result
    assert "2 个市场" in result
    assert "Binance · BTCUSDT · 当前有效" in result
    assert "OKX · BTC-USDT · 当前有效" in result


def test_missing_market_guides_catalog_setup_and_preserves_original_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        market,
        "load_catalog_setup_plan",
        lambda _state, goal: {
            "goal": goal.to_request(),
            "availability": "not_configured",
            "activity": "idle",
            "recommended_option": 0,
            "blockers": ["missing_connection_binding"],
            "options": [
                {
                    "binding": {"provider": "massive", "source": "equity"},
                    "recommendation": "recommended",
                    "actual_scope": "provider_catalog",
                    "requires_connection": True,
                    "connection_binding_present": False,
                    "already_configured": False,
                    "reasons": ["supported_product"],
                    "limitations": [
                        "product_is_provider_specific",
                        "requires_provider_account",
                    ],
                }
            ],
        },
    )

    async def run() -> tuple[
        tuple[str, ...], str, str | None, tuple[tuple[str, ...], ...]
    ]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            assert screen.session.context == ("market", "missing")
            screen.submit("1")
            screen.submit("1")
            screen.submit("1")
            await pilot.pause(0.1)
            return (
                screen.session.context,
                interaction_copy_text(screen.session.interaction),
                screen.session.market.query,
                tuple(frame.context for frame in screen.session.navigation_stack),
            )

    context, interaction, query, stack = asyncio.run(run())

    assert context == ("market", "catalog-setup")
    assert query == "AAPL"
    assert "该服务商的产品目录" in interaction
    assert "不代表完整美国股票或交易所上市目录" in interaction
    assert "配置所需的数据服务账号" in interaction
    assert ("market", "missing") in stack
    assert ("market", "catalog-exchange") in stack
    assert ("market", "catalog-instrument") in stack


@pytest.mark.parametrize(
    ("query", "expected_choices", "selected_query"),
    [
        (
            "上证",
            ("上海证券交易所", "上证综合指数", "浏览上交所上市股票"),
            "上证综合指数",
        ),
        (
            "深证",
            ("深圳证券交易所", "深证成份指数", "浏览深交所上市股票"),
            "深证成份指数",
        ),
        (
            "美国股票",
            ("浏览美国上市股票", "浏览美国成交市场", "浏览服务商股票目录"),
            "美国 股票 市场",
        ),
    ],
)
def test_ambiguous_market_language_requires_an_explicit_intent_choice(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_choices: tuple[str, ...],
    selected_query: str,
) -> None:
    searches: list[str] = []

    def search(_state: object, _kind: str, value: str):
        searches.append(value)
        return ()

    monkeypatch.setattr(market, "load_records", search)

    async def run() -> tuple[tuple[str, ...], str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit(f"/market {query}")
            await pilot.pause(0.05)
            assert screen.session.context == ("market", "intent")
            visible = interaction_copy_text(screen.session.interaction)
            screen.submit("2")
            await pilot.pause(0.1)
            return screen.session.context, visible

    context, visible = asyncio.run(run())

    assert all(choice in visible for choice in expected_choices)
    assert searches == [selected_query]
    assert context == ("market", "missing")


def test_catalog_account_setup_returns_to_preparation_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        market,
        "load_catalog_setup_plan",
        lambda _state, goal: {
            "goal": goal.to_request(),
            "availability": "not_configured",
            "activity": "idle",
            "recommended_option": 0,
            "blockers": ["missing_connection_binding"],
            "options": [
                {
                    "binding": {"provider": "massive", "source": "equity"},
                    "actual_scope": "provider_catalog",
                    "requires_connection": True,
                    "connection_binding_present": False,
                    "already_configured": False,
                    "reasons": [],
                    "limitations": [],
                }
            ],
        },
    )

    async def run() -> tuple[tuple[str, ...], str, object]:
        state = _state()
        state.dry_run = True
        state.no_exec = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("/market AAPL", "1", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.05)
            screen.submit("1")
            await pilot.pause(0.05)
            assert screen.session.context == ("resources", "setup")
            for value in ("", "", "2", "test-key"):
                screen.submit(value)
                await pilot.pause(0.05)
            return (
                screen.session.context,
                interaction_copy_text(screen.session.interaction),
                (
                    screen.session.market.catalog_setup_plan.connection_id
                    if screen.session.market.catalog_setup_plan is not None
                    else None
                ),
            )

    context, interaction, connection_id = asyncio.run(run())

    assert context == ("market", "catalog-setup")
    assert "开始准备" in interaction
    assert "配置所需的数据服务账号" not in interaction
    assert connection_id == "massive-equity-credential"


def test_catalog_preparation_automatically_returns_to_original_market_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    searches = 0

    def load_records(*_args, **_kwargs):
        nonlocal searches
        searches += 1
        return () if searches == 1 else (_market(),)

    initial_plan = {
        "goal": {
            "kind": "exchange_instruments",
            "exchange_id": "exchange:nasdaq",
            "instrument_kind": "equity",
        },
        "availability": "not_configured",
        "activity": "idle",
        "recommended_option": 0,
        "blockers": [],
        "options": [
            {
                "binding": {"provider": "massive", "source": "equity"},
                "actual_scope": "provider_catalog",
                "requires_connection": True,
                "connection_binding_present": True,
                "already_configured": True,
                "reasons": [],
                "limitations": [],
            }
        ],
        "_connection_id": "massive-main-credential",
    }
    usable_plan = {**initial_plan, "availability": "usable"}
    monkeypatch.setattr(market, "load_records", load_records)
    monkeypatch.setattr(
        market,
        "load_catalog_setup_plan",
        lambda _state, _goal: dict(initial_plan),
    )
    monkeypatch.setattr(
        market,
        "prepare_catalog_source",
        lambda *_args, **_kwargs: {"plan": dict(usable_plan)},
    )

    async def run() -> tuple[tuple[str, ...], str, int]:
        state = _state()
        state.yes = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("/market AAPL", "1", "1", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.06)
            await pilot.pause(0.1)
            return (
                screen.session.context,
                interaction_copy_text(screen.session.interaction),
                searches,
            )

    context, interaction, search_count = asyncio.run(run())

    assert context == ("market", "results")
    assert "AAPL · 股票" in interaction
    assert search_count == 2


def test_catalog_completion_does_not_steal_an_unrelated_page() -> None:
    session = GuidedSession(root_label="trader")
    session.market.query = "AAPL"
    session.enter("market", "missing")
    session.enter("market", "catalog-exchange")
    session.enter("market", "catalog-instrument")
    session.enter("market", "catalog-setup")
    session.enter("research")
    spec = OperationSpec.create(
        action_name="market.catalog.check",
        audit_summary="检查标的目录准备条件",
        route=ResultRoute(ResultKind.MARKET_CATALOG_SETUP),
        operation=lambda: None,
        running_status="正在检查…",
    )

    effects = market.handle_success(
        _state(),
        session,
        spec,
        {
            "availability": "usable",
            "activity": "idle",
            "recommended_option": None,
            "blockers": [],
            "options": [],
        },
    )

    assert effects is not None
    assert len(effects) == 1
    assert session.context == ("research",)

    session.enter("market")
    assert any(item.id == "resume-search" for item in context_items(session, _state()))
    resume = market.handle_context(_state(), session, "1")

    assert resume is not None
    assert len(resume) == 1
    assert isinstance(resume[0], RunOperation)


def test_catalog_setup_recovers_reference_and_resumes_original_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    searches = 0
    plans = 0

    def load_records(*_args: object, **_kwargs: object) -> tuple[Market, ...]:
        nonlocal searches
        searches += 1
        if searches == 1:
            raise RuntimeError("reference catalog is not initialized")
        return (_market(),)

    def load_plan(_state: object, goal: object) -> dict[str, object]:
        nonlocal plans
        plans += 1
        return {
            "goal": goal.to_request(),
            "availability": "usable",
            "activity": "idle",
            "recommended_option": None,
            "blockers": [],
            "options": [],
        }

    starts: list[tuple[str, str]] = []
    monkeypatch.setattr(market, "load_records", load_records)
    monkeypatch.setattr(market, "load_catalog_setup_plan", load_plan)
    monkeypatch.setattr(WorkbenchState, "refresh_snapshot", lambda self: self.snapshot)
    monkeypatch.setattr(
        operations_actions,
        "execute_service",
        lambda _state, component, action: starts.append((component, action)) or {},
    )

    async def run() -> tuple[tuple[str, ...], str, str, str]:
        state = _state()
        state.yes = True
        state.snapshot = ObserveSnapshot(
            workspace_id="trader",
            shared_services={
                "reference": {
                    "status": "not_running",
                    "control_reachable": False,
                    "control_socket_exists": False,
                    "pid_alive": False,
                },
                "market": {"status": "not_running"},
            },
        )
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("/market AAPL", "1", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.06)
            unavailable = interaction_copy_text(screen.session.interaction)
            status = str(screen.query_one("#command-status", Static).render())
            screen.submit("1")
            await pilot.pause(0.2)
            return (
                screen.session.context,
                unavailable,
                status,
                interaction_copy_text(screen.session.interaction),
            )

    context, unavailable, status, final_interaction = asyncio.run(run())

    assert "标的目录服务尚未就绪" in unavailable
    assert "启动 Reference 并继续" in unavailable
    assert "Errno 2" not in unavailable
    assert status == "Reference 服务尚未就绪 · 请选择恢复方式"
    assert starts == [("reference", "start")]
    assert plans == 1
    assert searches == 2
    assert context == ("market", "results")
    assert "AAPL · 股票" in final_interaction


def test_unresponsive_reference_offers_diagnostics_without_unsafe_restart() -> None:
    session = GuidedSession(root_label="trader")
    session.enter("market", "catalog-setup")
    session.market.catalog_setup_reference_issue = "unresponsive"
    session.market.catalog_setup_reference_recovery = None

    actions = context_items(session, _state())

    assert "recover-reference" not in {item.id for item in actions}
    assert {item.id for item in actions} >= {
        "check",
        "reference-details",
        "change",
    }


def test_market_result_can_be_focused_and_opened_with_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market, "load_records", lambda *args, **kwargs: (_market(),))

    async def run() -> tuple[tuple[str, ...], bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market AAPL")
            await pilot.pause(0.1)

            await pilot.press("tab", "enter")
            await pilot.pause()
            return (
                screen.session.context,
                screen.query_one("#guided-actions", ActionList).has_focus,
            )

    context, actions_focused = asyncio.run(run())

    assert context == ("market", "selected")
    assert actions_focused


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

            for value in ("1", "5", "5"):
                screen.submit(value)
                await pilot.pause(0.1)
            assert (
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder
                == "输入代码或名称；直接回车浏览"
            )

            await pilot.press("a", "a", "p", "l", "enter")
            await pilot.pause(0.3)
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
    assert context == "trader › 标的目录 › 查询结果"
    assert option_count == 1
    assert input_focused
    assert "找到 1 条交易标的记录" not in output
    assert status == "找到 1 个结果 · 请选择"


def test_uninitialized_reference_search_offers_guided_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object, **__: object) -> tuple[Market, ...]:
        raise RuntimeError("reference catalog is not initialized")

    monkeypatch.setattr(reference, "load_records", fail)

    async def run() -> tuple[tuple[str, ...], str, str, str | None]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)

            for value in ("1", "5", "5"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("AAPL")
            await pilot.pause(0.3)
            return (
                screen.session.context,
                interaction_copy_text(screen.session.interaction),
                str(screen.query_one("#command-status", Static).render()),
                screen.session.market.query,
            )

    context, interaction, status, query = asyncio.run(run())

    assert context == ("market", "missing")
    assert "还没有可查询的标的目录" in interaction
    assert "未找到标的" not in interaction
    assert "准备这个标的目录" in interaction
    assert "SQLite" not in interaction
    assert status == "标的目录尚未准备"
    assert query == "AAPL"


def _reference_runtime_status() -> ReferenceRuntimeStatusResponse:
    return ReferenceRuntimeStatusResponse.from_mapping(
        {
            "status": "ready",
            "app_runtime": {
                "phase": "serving",
                "actor_id": "reference",
                "source_id": "reference-default",
                "refresh_interval_millis": 60_000,
                "active_work_item_count": 0,
                "queued_work_item_count": 1,
                "last_tick_finished_unix_nanos": 1_777_777_777_000_000_000,
                "last_tick_duration_millis": 18,
                "next_tick_due_unix_nanos": 1_777_777_837_000_000_000,
            },
            "catalog": {
                "readiness": "ready",
                "generation": 42,
                "event_sequence": 9810,
                "exchange_count": 4,
                "asset_count": 20,
                "instrument_count": 30,
                "listing_count": 31,
                "market_count": 32,
                "active_market_count": 29,
                "integrity": {"degraded": False},
            },
            "sources": [
                {
                    "source_id": "binance-spot",
                    "provider_id": "binance",
                    "enabled": True,
                    "paused": False,
                    "phase": "ready",
                    "progress": {
                        "kind": "complete",
                        "pages_done": 2,
                        "pages_total": 2,
                        "records_seen": 1200,
                        "records_changed": 3,
                    },
                    "last_success_unix_nanos": 1_777_777_777_000_000_000,
                    "consecutive_failures": 0,
                    "stale": False,
                    "has_last_known_good": True,
                },
                {
                    "source_id": "massive-options",
                    "provider_id": "massive",
                    "enabled": True,
                    "paused": False,
                    "phase": "degraded",
                    "progress": {"kind": "unknown"},
                    "last_success_unix_nanos": None,
                    "consecutive_failures": 2,
                    "stale": True,
                    "has_last_known_good": False,
                    "last_error": {
                        "code": "reference.provider_failed",
                        "retryable": True,
                        "message": "HTTP 429",
                    },
                },
            ],
            "publication": {
                "pending_publication_count": 2,
                "backlog_degraded": False,
                "oldest_pending_event_id": "reference:42",
            },
            "diagnostics": [
                {
                    "severity": "warn",
                    "code": "reference.source_retrying",
                    "message": "Massive source is retrying",
                }
            ],
        }
    )


def test_binance_stocks_setup_goal_is_provider_product_not_exchange() -> None:
    goal = reference_actions.CatalogSetupGoal.provider_product("binance", "equity")

    assert goal.to_request() == {
        "kind": "provider_product",
        "binding": {"provider": "binance", "source": "equity"},
    }

    plan = reference_actions.CatalogSetupPlanView.from_mapping(
        {
            "goal": goal.to_request(),
            "availability": "not_configured",
            "activity": "idle",
            "recommended_option": 0,
            "blockers": ["missing_connection_binding"],
            "options": [
                {
                    "binding": {"provider": "binance", "source": "equity"},
                    "actual_scope": "provider_catalog",
                    "requires_connection": True,
                    "connection_binding_present": False,
                    "limitations": [
                        "requires_provider_account",
                        "product_is_provider_specific",
                    ],
                }
            ],
        }
    )
    with Console(width=100, record=True) as console:
        console.print(reference_actions.catalog_setup_renderable(plan))
    output = console.export_text()
    assert "该服务商的产品目录" in output
    assert "不代表完整美国股票或交易所上市目录" in output


def test_reference_results_use_user_vocabulary_and_hide_internal_ids() -> None:
    instrument = SimpleNamespace(
        id="instrument:equity:US:AAPL:common",
        symbol="AAPL",
        name="Apple Inc.",
        instrument_type="equity",
        status="active",
        expiry_unix_nanos=None,
        strike=None,
        option_right=None,
        underlying_instrument_id=None,
    )

    with Console(width=100, record=True) as console:
        console.print(
            reference_actions.records_renderable("instruments", (instrument,))
        )
        console.print(reference_actions.detail_renderable(instrument, "instruments"))
    output = console.export_text()

    assert "股票" in output
    assert "当前有效" in output
    assert "equity" not in output
    assert "instrument:equity:US:AAPL:common" not in output


def test_trading_access_search_resolves_instrument_then_checks_both_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = SimpleNamespace(
        id="instrument:equity:US:AAPL:common",
        symbol="AAPL",
        name="Apple Inc.",
        instrument_type="equity",
        status="active",
    )
    market_record = SimpleNamespace(
        execution_venue_id="venue:xnas",
        venue_symbol="AAPL",
        instrument_id=instrument.id,
        status="active",
    )
    availability = SimpleNamespace(
        source_id="binance-equity",
        status="active",
    )
    evidence = SimpleNamespace(conclusion="known")

    class Catalog:
        def search_instruments(self, **filters: Any) -> Any:
            assert filters["query"] == "AAPL"
            return SimpleNamespace(instruments=(instrument,), evidence=evidence)

        def find_venue_markets(self, **filters: Any) -> Any:
            assert filters["instrument_id"] == instrument.id
            return SimpleNamespace(markets=(market_record,))

        def find_provider_catalog_memberships(self, **filters: Any) -> tuple[Any, ...]:
            assert filters["instrument_ids"] == (instrument.id,)
            return (availability,)

    monkeypatch.setattr(reference_actions, "_application", lambda _state: Catalog())

    records = reference_actions.load_records(object(), "trading-access", "AAPL")
    related_kind, related = reference_actions.load_instrument_markets(
        object(), records[0]
    )
    with Console(width=100, record=True) as console:
        console.print(reference_actions.records_renderable(related_kind, related))
    output = console.export_text()

    assert tuple(records) == (instrument,)
    assert "实际成交场所" in output
    assert "服务商目录覆盖" in output
    assert "币安股票产品" in output
    assert "不代表当前账号已有行情或下单权限" in output


def test_missing_trading_access_enters_the_catalog_preparation_flow() -> None:
    session = GuidedSession(root_label="trader")
    session.enter("reference")
    session.reference.query = "AAPL"
    spec = OperationSpec.create(
        action_name="reference.find.trading-access",
        audit_summary="查看 AAPL 在哪里可以交易",
        route=ResultRoute(ResultKind.REFERENCE_RECORDS, "trading-access"),
        operation=lambda: (),
        running_status="正在查找…",
    )

    effects = reference.handle_success(_state(), session, spec, ())

    assert effects is not None
    assert session.context == ("market", "missing")
    assert session.market.query == "AAPL"
    assert tuple(item.id for item in context_items(session, _state())) == (
        "prepare",
        "retry",
        "catalog",
    )


@pytest.mark.parametrize(
    ("conclusion", "expected_copy"),
    (
        ("not_found_in_covered_scope", "在当前已完整覆盖的范围内未找到匹配记录"),
        ("preparing", "相关目录正在准备"),
        ("known_but_stale", "只找到陈旧的目录覆盖"),
        ("source_unavailable", "相关目录来源当前不可用"),
        ("unknown_outside_coverage", "当前目录覆盖尚不足以判断"),
        (None, "当前目录覆盖尚不足以判断"),
    ),
)
@pytest.mark.parametrize("market_search", (False, True))
def test_empty_trading_access_preserves_coverage_conclusion(
    conclusion: str | None, expected_copy: str, market_search: bool
) -> None:
    session = GuidedSession(root_label="trader")
    session.enter("reference")
    session.reference.query = "AAPL"
    session.market.query = "AAPL"
    spec = OperationSpec.create(
        action_name="reference.find.trading-access",
        audit_summary="查询 AAPL",
        route=(
            ResultRoute(ResultKind.MARKET)
            if market_search
            else ResultRoute(ResultKind.REFERENCE_RECORDS, "trading-access")
        ),
        operation=lambda: (),
        running_status="正在查找…",
    )
    result = reference_actions.CatalogSearchResult(
        records=(),
        evidence=(
            SimpleNamespace(conclusion=conclusion) if conclusion is not None else None
        ),
    )

    handler = market.handle_success if market_search else reference.handle_success
    effects = handler(_state(), session, spec, result)

    assert effects is not None
    covered_empty = conclusion == "not_found_in_covered_scope"
    assert session.context == (
        "market", "not-found" if covered_empty else "missing"
    )
    assert session.market.query == "AAPL"
    copy = interaction_copy_text(session.interaction)
    assert expected_copy in copy
    assert "未找到标的" not in copy
    if conclusion != "not_found_in_covered_scope":
        assert "已完整覆盖的范围内未找到" not in copy
    assert tuple(item.id for item in context_items(session, _state())) == (
        ("retry", "catalog") if covered_empty else ("prepare", "retry", "catalog")
    )


@pytest.mark.parametrize(
    ("conclusion", "expected_copy"),
    (
        ("found", "本次查询开始时的最新已提交版本"),
        ("known_but_stale", "目录覆盖已陈旧"),
        ("source_unavailable", "目录来源当前不可用"),
        ("preparing", "目录正在准备"),
    ),
)
@pytest.mark.parametrize("market_search", (False, True))
def test_reference_records_preserve_knowledge_warning(
    conclusion: str, expected_copy: str, market_search: bool
) -> None:
    session = GuidedSession(root_label="trader")
    session.enter("reference")
    spec = OperationSpec.create(
        action_name="reference.find.markets",
        audit_summary="查询标的",
        route=(
            ResultRoute(ResultKind.MARKET)
            if market_search
            else ResultRoute(ResultKind.REFERENCE_RECORDS, "markets")
        ),
        operation=lambda: (),
        running_status="正在查找…",
    )
    result = reference_actions.CatalogSearchResult(
        records=(_market(),), evidence=SimpleNamespace(conclusion=conclusion)
    )

    handler = market.handle_success if market_search else reference.handle_success
    effects = handler(_state(), session, spec, result)

    assert effects is not None
    interactions = [effect for effect in effects if isinstance(effect, SetInteraction)]
    assert len(interactions) == 1
    assert expected_copy in interaction_copy_text(interactions[0].interaction)
    assert len(session.visible_records) == 1


def test_reference_source_management_uses_owner_status_and_page_stack() -> None:
    session = GuidedSession(root_label="trader")
    session.enter("reference")
    spec = OperationSpec.create(
        action_name="reference.status",
        audit_summary="管理目录来源",
        route=ResultRoute(ResultKind.REFERENCE_STATUS, "sources"),
        operation=lambda: None,
        running_status="正在读取…",
    )

    effects = reference.handle_success(
        _state(), session, spec, _reference_runtime_status()
    )

    assert effects is not None
    assert session.context == ("reference", "sources")
    assert len(session.visible_records) == 2
    assert session.visible_records[0].label == "美国股票期权目录"
    assert "Massive（美国证券目录）" in session.visible_records[0].description
    assert "retrying" not in session.visible_records[0].description
    assert session.stack_parent() == ("reference",)
    assert context_items(session, _state())[-1].id == "add"

    selected = reference.handle_context(_state(), session, "1")
    assert selected is not None
    assert session.context == ("reference", "source-selected")
    assert session.stack_parent() == ("reference", "sources")
    action_ids = tuple(item.id for item in context_items(session, _state()))
    assert action_ids == ("refresh", "pause", "progress")


def test_reference_runtime_status_has_structured_owner_sections() -> None:
    with Console(width=120, record=True) as console:
        console.print(runtime_status_renderable(_reference_runtime_status()))
    output = console.export_text()

    assert "标的目录存在需要处理的状态" in output
    assert "准备活动" in output
    assert "交易品种 30" in output
    assert "币安现货" in output
    assert "美国股票期权目录" in output
    assert output.index("美国股票期权目录") < output.index("币安现货")
    assert "HTTP 429 · 可重试" in output
    assert "可到运行中心查看技术详情" in output


def test_reference_status_runs_from_existing_reference_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reference, "load_runtime_status", lambda _state: _reference_runtime_status()
    )

    async def run() -> tuple[str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(120, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("reference")
            await pilot.pause()
            screen.submit("2")
            await pilot.pause(0.3)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    output, status, focused = asyncio.run(run())
    assert "标的目录存在需要处理的状态" in output
    assert "币安现货" in output
    assert status == "标的目录准备状态已就绪"
    assert focused


def test_workspace_market_status_visualizes_process_and_data_plane() -> None:
    rendered = workspace_status_renderable(
        {
            "process": {
                "status": "running",
                "control_reachable": True,
                "pid": 42,
            },
            "health": {
                "status": "ready",
                "feed_status": "ready",
                "actor_id": "market-actor",
                "event_sequence": 91,
                "current_view_input_update_count": 90,
                "current_view_commit_count": 80,
                "current_view_encoded_update_count": 79,
                "current_view_order_book_encode_count": 8,
                "last_current_view_commit_latency_nanos": 2_500_000,
                "notification_attempt_count": 90,
                "notification_failure_count": 1,
            },
        }
    )
    with Console(width=110, record=True) as console:
        console.print(rendered)
    output = console.export_text()

    assert "Market 服务与数据面均已就绪" in output
    assert "进程" in output
    assert "数据面" in output
    assert "market-actor" in output
    assert "输入 90 · 提交 80 · 编码 79 · 订单簿 8" in output
    assert "2.500 ms" in output
    assert "失败 1" in output


def test_workspace_market_routes_are_aggregated_by_provider_and_state() -> None:
    rendered = workspace_routes_renderable(
        {
            "routes": [
                {
                    "market_id": "market:btc-usdt",
                    "provider": "binance",
                    "state": "ready",
                    "selected": True,
                    "observation_kinds": ["quote", "trade"],
                },
                {
                    "market_id": "market:eth-usdt",
                    "provider": "binance",
                    "state": "ready",
                    "selected": False,
                    "observation_kinds": ["quote"],
                },
                {
                    "market_id": "market:aapl",
                    "provider": "massive",
                    "state": "degraded",
                    "selected": False,
                    "observation_kinds": ["quote"],
                },
            ]
        }
    )
    with Console(width=100, record=True) as console:
        console.print(rendered)
    output = console.export_text()

    assert "已配置 3 条数据路由，覆盖 2 个 Provider" in output
    assert "Provider 路由" in output
    assert "binance" in output
    assert "ready" in output
    assert "quote, trade · 已选 1" in output
    assert "massive" in output
    assert "degraded" in output


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
    assert prompt_context == "trader › 市场与标的"
    assert not prompt_actions_visible
    assert prompt_hints == "Enter 搜索  ·  Esc 返回  ·  Ctrl+P 命令  ·  Tab 切换区域"
    assert context == "trader › 市场与标的 › 查询结果"
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
    assert context == "trader › 市场与标的 › 查询结果"
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
    assert "买盘 BID" in output
    assert "卖盘 ASK" in output
    assert "226.50" in output
    assert "226.75" in output
    assert "massive · REST · Provider 直连" in output
    assert "市场时间" in output
    assert "获取时间" in output
    assert "数据年龄  1.0 秒" in output
    assert "较旧" not in output
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
    assert context == "AAPL › 行情 › 已选标的"
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


def test_stale_quote_makes_data_age_visible_without_obscuring_prices() -> None:
    rendered = observation_renderable(
        {
            "symbol": "AAPL",
            "data_type": "quote",
            "provider": "massive",
            "bid_price": "309.51",
            "ask_price": "309.87",
            "bid_quantity": "80",
            "ask_quantity": "480",
            "observed_at_unix_nanos": 1_787_733_778_000_000_000,
            "_fetched_at_unix_nanos": 1_787_733_834_800_000_000,
            "_source_mode": "provider-direct",
            "_transport": "REST",
        }
    )
    with Console(width=80, record=True) as console:
        console.print(rendered)
    output = console.export_text()

    assert "买盘 BID" in output
    assert "309.51" in output
    assert "数量  80" in output
    assert "56.8 秒  ⚠ 较旧" in output


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
            for value in ("1", "3", "AAPL", "1", "", "", "", "history/aapl.jsonl"):
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
    assert context == "AAPL › 行情 › 已选标的"
    assert "Market 文件操作预演完成，未执行任何修改" in output
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
            for value in ("1", "5", "7", "USD", "1", "3"):
                screen.submit(value)
                await pilot.pause(0.15)
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
    assert selected == "USD › 标的目录 › 目录记录"
    assert results == "trader › 标的目录 › 查询结果"
    assert "asset:usd" in output
    assert focused


def test_guided_reference_instrument_type_is_an_explicit_input_step() -> None:
    async def run() -> tuple[object, str | None, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("1", "5", "4", "5"):
                screen.submit(value)
                await pilot.pause(0.1)
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
