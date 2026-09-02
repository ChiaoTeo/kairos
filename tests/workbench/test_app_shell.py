from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest
from rich.text import Text
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static

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
from kairospy.surface.workbench.screens.activity import (
    ActivityKind,
    ActivityOutcome,
    ActivityRecord,
)
from kairospy.surface.workbench.screens.flows import market
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionItem,
    ActionList,
    ChoiceInteraction,
    ConfirmInteraction,
    Feature,
    InputInteraction,
    InteractionHeading,
    InteractionRegion,
    RunningInteraction,
    WorkbenchCommandInput,
    interaction_copy_text,
)
from app_support import (
    log_text as _log_text,
    market as _market,
    workbench_state as _state,
)


def test_workbench_enables_mouse_activity_focus_by_default(monkeypatch) -> None:
    runs: list[dict[str, object]] = []
    monkeypatch.setattr(App, "run", lambda self, **kwargs: runs.append(kwargs))

    KairosWorkbenchApp(_state()).run()
    KairosWorkbenchApp(_state()).run(mouse=False)

    assert runs == [{"mouse": True}, {"mouse": False}]


def test_compact_interaction_heading_is_one_line_without_panel_chrome() -> None:
    copy = interaction_copy_text(
        ChoiceInteraction(
            heading=InteractionHeading("paper-demo", "paper · 运行条件"),
            state="需要处理",
            actions=(ActionItem("retry", "重新校验", "修复后再次检查", "1"),),
        )
    )

    assert copy.splitlines()[0] == "paper-demo  ·  paper · 运行条件"
    assert "╭" not in copy
    assert "[1] 重新校验" in copy


def test_workbench_starts_as_one_guided_command_screen() -> None:
    async def run() -> tuple[bool, str, str, str, str, int, bool, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            output = _log_text(app.screen.query_one("#command-output", RichLog))
            workspace_title = str(
                app.screen.query_one("#workspace-title", Static).render()
            )
            context = str(app.screen.query_one("#command-context", Static).render())
            actions = app.screen.query_one("#guided-actions", ActionList)
            return (
                isinstance(app.screen, CommandLineScreen),
                app.screen.sub_title or "",
                output,
                workspace_title,
                context,
                actions.option_count,
                actions.can_focus,
                command_input.has_focus,
            )

    (
        is_command_screen,
        subtitle,
        output,
        workspace_title,
        context,
        option_count,
        actions_can_focus,
        input_focused,
    ) = asyncio.run(run())

    assert is_command_screen
    assert subtitle == "命令"
    assert output == ""
    assert workspace_title == "KAIROS  ·  trader"
    assert context == "trader"
    assert option_count == 7
    assert actions_can_focus
    assert input_focused


def test_interaction_panel_toggles_default_and_collapsed_with_ctrl_o() -> None:
    async def run() -> tuple[
        tuple[bool, bool, bool, str],
        tuple[bool, bool, bool],
    ]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen.query_one("#command-output", RichLog)
            interaction = screen.query_one("#interaction-region", InteractionRegion)
            summary = screen.query_one("#interaction-collapsed-summary", Static)

            await pilot.press("ctrl+o")
            await pilot.pause()
            collapsed = (
                screen.has_class("interaction-collapsed"),
                interaction.display,
                summary.display,
                str(summary.render()),
            )

            await pilot.press("ctrl+o")
            await pilot.pause()
            restored = (
                screen.has_class("interaction-collapsed"),
                output.display,
                interaction.display,
            )
            return collapsed, restored

    collapsed, restored = asyncio.run(run())

    assert collapsed[:3] == (True, False, True)
    assert "Ctrl+O 恢复" in collapsed[3]
    assert restored == (False, True, True)


def test_panel_command_is_available_during_confirmation_and_keeps_summary() -> None:
    async def run() -> tuple[str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.request_confirmation("部署策略", lambda: None)
            screen.submit("/panel collapsed")
            await pilot.pause()
            summary = str(
                screen.query_one("#interaction-collapsed-summary", Static).render()
            )
            mode = screen.session.interaction.mode.value
            screen.submit("/panel default")
            await pilot.pause()
            status = str(screen.query_one("#command-status", Static).render())
            return summary, mode, status, screen._input().has_focus

    summary, interaction_mode, status, input_focused = asyncio.run(run())

    assert "等待确认 · 需要确认 · Ctrl+O 恢复" in summary
    assert interaction_mode == "confirm"
    assert status == "交互区已恢复默认大小"
    assert input_focused


def test_workspace_header_uses_compact_status_below_100_columns() -> None:
    async def run() -> tuple[bool, bool, str, bool, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._set_status("已选择 12 条 Activity · C 复制")
            await pilot.pause()
            full = screen.query_one("#command-status", Static)
            compact = screen.query_one("#compact-command-status", Static)
            compact_state = (full.display, compact.display, str(compact.render()))

            await pilot.resize_terminal(100, 30)
            await pilot.pause()
            return (*compact_state, full.display, compact.display)

    compact_full, compact_visible, compact_text, wide_full, wide_compact = asyncio.run(
        run()
    )

    assert not compact_full
    assert compact_visible
    assert compact_text == "已选 12 条"
    assert wide_full
    assert not wide_compact


def test_workspace_header_divider_follows_theme_primary_color() -> None:
    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            header = app.screen.query_one("#workspace-header")
            nord_border = str(header.styles.border_bottom)
            app.theme = "kairos-dracula"
            await pilot.pause()
            return nord_border, str(header.styles.border_bottom)

    nord_border, dracula_border = asyncio.run(run())

    assert "Color(136, 192, 208, a=0.45)" in nord_border
    assert "Color(189, 147, 249, a=0.45)" in dracula_border


def test_focusing_interaction_controls_does_not_tint_the_whole_region() -> None:
    async def run() -> tuple[int, float, int, bool, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            input_tint_alpha = command_input.styles.background_tint.a
            input_highlight_alpha = command_input.styles.background.a
            await pilot.press("tab")
            actions = app.screen.query_one("#guided-actions", ActionList)
            return (
                input_tint_alpha,
                input_highlight_alpha,
                actions.styles.background_tint.a,
                actions.has_focus,
                command_input.region.right == actions.content_region.right,
            )

    (
        input_tint_alpha,
        input_highlight_alpha,
        actions_tint_alpha,
        actions_focused,
        right_edges_aligned,
    ) = asyncio.run(run())

    assert input_tint_alpha == 0
    assert input_highlight_alpha == pytest.approx(0.22)
    assert actions_tint_alpha == 0
    assert actions_focused
    assert right_edges_aligned


def test_copy_page_copies_complete_redacted_output_for_agent() -> None:
    async def run() -> tuple[str, tuple[dict[str, object], ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            log = app.screen.query_one("#command-output", RichLog)
            log.write("api_key=should-not-leak")
            await pilot.press("ctrl+shift+c")
            return app._clipboard, app.transcript.events

    clipboard, events = asyncio.run(run())

    assert "Workspace: trader" in clipboard
    assert "## 当前交互" in clipboard
    assert "api_key=<redacted>" in clipboard
    assert "should-not-leak" not in clipboard
    assert any(event["event"] == "page_copied" for event in events)


def test_interaction_region_copies_independently_by_focus_and_command() -> None:
    async def run() -> tuple[str, str, str, str, tuple[dict[str, object], ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            expected = interaction_copy_text(screen.session.interaction)

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("super+c")
            await pilot.pause()
            shortcut_copy = app._clipboard

            app._clipboard = ""
            await pilot.press("ctrl+shift+c")
            await pilot.pause()
            terminal_shortcut_copy = app._clipboard

            app._clipboard = ""
            screen.submit("/copy-interaction")
            await pilot.pause()
            command_copy = app._clipboard
            return (
                expected,
                shortcut_copy,
                terminal_shortcut_copy,
                command_copy,
                app.transcript.events,
            )

    expected, shortcut_copy, terminal_shortcut_copy, command_copy, events = asyncio.run(
        run()
    )

    assert shortcut_copy == expected
    assert terminal_shortcut_copy == expected
    assert command_copy == expected
    assert "Workspace:" not in shortcut_copy
    assert any(event["event"] == "interaction_copied" for event in events)


def test_tab_focuses_activity_stream_and_space_multiselects_for_copy() -> None:
    async def run() -> tuple[bool, int, str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(1, 4):
                output.append_activity(
                    ActivityRecord(
                        activity_id=f"activity-{index}",
                        kind=ActivityKind.QUERY,
                        outcome=ActivityOutcome.SUCCESS,
                        title=f"结果 {index}",
                        copy_text=f"payload {index}",
                    )
                )
            await pilot.pause()
            await pilot.press("tab", "tab")
            await pilot.pause()
            await pilot.press("home", "down", "space", "down", "space", "c")
            await pilot.pause()
            copied_with_c = app._clipboard
            app._clipboard = ""
            await pilot.press("super+c")
            await pilot.pause()
            return (
                output.has_focus,
                output.selected_count,
                copied_with_c,
                app._clipboard,
                str(screen.query_one("#command-status", Static).render()),
            )

    focused, selected_count, copied_with_c, copied_with_command_c, status = asyncio.run(
        run()
    )

    assert focused
    assert selected_count == 2
    assert copied_with_command_c == copied_with_c
    assert "[A002] 结果 2" in copied_with_c
    assert "[A003] 结果 3" in copied_with_c
    assert "结果 1" not in copied_with_c
    assert status == "已选择 2 条 Activity · C 复制"


def test_mouse_focuses_activity_and_modifier_clicks_select_a_range() -> None:
    async def run() -> tuple[
        bool, int, tuple[str, ...], int | None, int, tuple[str, ...]
    ]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(1, 4):
                output.append_activity(
                    ActivityRecord(
                        activity_id=f"mouse-{index}",
                        kind=ActivityKind.QUERY,
                        outcome=ActivityOutcome.SUCCESS,
                        title=f"鼠标结果 {index}",
                    )
                )
            await pilot.pause()
            output.scroll_home(animate=False, immediate=True)

            def click_y(index: int) -> int:
                rendered_range = output._rendered_ranges[index]
                return rendered_range.start - int(output.scroll_y)

            await pilot.click(output, offset=(2, click_y(0)))
            single_count = output.selected_count
            single_titles = tuple(
                activity.title for activity in output.selected_activities()
            )
            await pilot.click(output, offset=(2, click_y(1)), meta=True)
            await pilot.click(output, offset=(2, click_y(2)), shift=True)
            await pilot.pause()
            return (
                output.has_focus,
                single_count,
                single_titles,
                output.cursor_sequence,
                output.selected_count,
                tuple(activity.title for activity in output.selected_activities()),
            )

    (
        focused,
        single_count,
        single_titles,
        cursor_sequence,
        selected_count,
        selected_titles,
    ) = asyncio.run(run())

    assert focused
    assert single_count == 1
    assert single_titles == ("鼠标结果 1",)
    assert cursor_sequence == 3
    assert selected_count == 2
    assert selected_titles == ("鼠标结果 2", "鼠标结果 3")


def test_activity_click_toggles_selection_and_blur_clears_it() -> None:
    async def run() -> tuple[int, tuple[str, ...], int, int, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(1, 3):
                output.append_activity(
                    ActivityRecord(
                        activity_id=f"toggle-{index}",
                        kind=ActivityKind.QUERY,
                        outcome=ActivityOutcome.SUCCESS,
                        title=f"切换选择 {index}",
                    )
                )
            await pilot.pause()
            output.scroll_home(animate=False, immediate=True)

            def click_y(index: int) -> int:
                rendered_range = output._rendered_ranges[index]
                return rendered_range.start - int(output.scroll_y)

            await pilot.click(output, offset=(2, click_y(0)))
            await pilot.click(output, offset=(2, click_y(0)))
            count_after_second_click = output.selected_count
            targets_after_second_click = tuple(
                activity.activity_id for activity in output.copy_target_activities()
            )

            await pilot.click(output, offset=(2, click_y(1)))
            count_before_blur = output.selected_count
            app.set_focus(screen._input())
            await pilot.pause()
            return (
                count_after_second_click,
                targets_after_second_click,
                count_before_blur,
                output.selected_count,
                output.has_focus,
            )

    (
        count_after_second_click,
        targets_after_second_click,
        count_before_blur,
        count_after_blur,
        output_focused,
    ) = asyncio.run(run())

    assert count_after_second_click == 0
    assert targets_after_second_click == ()
    assert count_before_blur == 1
    assert count_after_blur == 0
    assert not output_focused


def test_focused_activity_stream_copies_default_target_without_full_tint() -> None:
    async def run() -> tuple[float, str, int | None, int, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            output.append_activity(
                ActivityRecord(
                    activity_id="focus-without-tint",
                    kind=ActivityKind.SYSTEM,
                    outcome=ActivityOutcome.NOTICE,
                    title="未选中的内容",
                )
            )
            await pilot.pause()
            await pilot.press("tab", "tab")
            await pilot.pause()
            await pilot.press("super+c")
            await pilot.pause()
            shortcut_copy = app._clipboard
            app._clipboard = ""
            screen.submit("/copy-selected")
            await pilot.pause()
            return (
                output.styles.background_tint.a,
                str(output.styles.background),
                output.cursor_sequence,
                output.selected_count,
                shortcut_copy,
                app._clipboard,
            )

    (
        tint_alpha,
        background,
        cursor_sequence,
        selected_count,
        shortcut_copy,
        command_copy,
    ) = asyncio.run(run())

    assert tint_alpha == 0
    assert background == "Color(46, 52, 64)"
    assert cursor_sequence == 1
    assert selected_count == 0
    assert "[A001] 未选中的内容" in shortcut_copy
    assert command_copy == shortcut_copy


def test_activity_focus_defaults_latest_then_restores_history_until_bottom() -> None:
    async def run() -> tuple[int | None, int | None, int | None]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(1, 4):
                output.append_activity(
                    ActivityRecord(
                        activity_id=f"history-{index}",
                        kind=ActivityKind.SYSTEM,
                        outcome=ActivityOutcome.NOTICE,
                        title=f"历史结果 {index}",
                    )
                )
            await pilot.pause()
            await pilot.press("tab", "tab")
            await pilot.pause()
            first_focus = output.cursor_sequence
            await pilot.press("up", "tab")
            output.append_activity(
                ActivityRecord(
                    activity_id="history-4",
                    kind=ActivityKind.SYSTEM,
                    outcome=ActivityOutcome.NOTICE,
                    title="历史结果 4",
                )
            )
            await pilot.press("tab", "tab")
            await pilot.pause()
            restored_focus = output.cursor_sequence
            screen.submit("/bottom")
            await pilot.pause()
            return first_focus, restored_focus, output.cursor_sequence

    first_focus, restored_focus, bottom_focus = asyncio.run(run())

    assert first_focus == 3
    assert restored_focus == 2
    assert bottom_focus == 4


def test_activity_commands_scroll_goto_and_copy_stable_ranges() -> None:
    async def run() -> tuple[float, float, bool, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(1, 4):
                output.append_activity(
                    ActivityRecord(
                        activity_id=f"activity-{index}",
                        kind=ActivityKind.QUERY,
                        outcome=ActivityOutcome.SUCCESS,
                        title=f"结果 {index}",
                        copy_text=(
                            "api_key=should-not-leak"
                            if index == 2
                            else f"payload {index}"
                        ),
                    )
                )
            for index in range(30):
                output.write(f"raw line {index}")
            await pilot.pause()
            initial_y = output.scroll_y
            screen.submit("/up 5")
            after_up = output.scroll_y
            screen.submit("/copy 1-2")
            copied = app._clipboard
            screen.submit("/goto A003")
            await pilot.pause()
            return (
                initial_y,
                after_up,
                output.has_focus,
                copied,
                str(screen.query_one("#command-status", Static).render()),
            )

    initial_y, after_up, focused, clipboard, status = asyncio.run(run())

    assert after_up == max(initial_y - 5, 0)
    assert focused
    assert "[A001] 结果 1" in clipboard
    assert "[A002] 结果 2" in clipboard
    assert "[A003]" not in clipboard
    assert "api_key=<redacted>" in clipboard
    assert "should-not-leak" not in clipboard
    assert status == "已定位到 Activity A003"


def test_activity_display_sequences_are_not_reused_after_clear() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            output.append_activity(
                ActivityRecord(
                    activity_id="before-clear",
                    kind=ActivityKind.SYSTEM,
                    outcome=ActivityOutcome.NOTICE,
                    title="清理前",
                )
            )
            screen.action_clear()
            output.append_activity(
                ActivityRecord(
                    activity_id="after-clear",
                    kind=ActivityKind.SYSTEM,
                    outcome=ActivityOutcome.NOTICE,
                    title="清理后",
                )
            )
            await pilot.pause()
            return output.export_plain_text(), output.activity_for_sequence(1) is None

    exported, first_was_removed = asyncio.run(run())

    assert first_was_removed
    assert "[A002] 清理后" in exported


def test_activity_view_commands_do_not_consume_a_guided_input_prompt() -> None:
    async def run() -> tuple[bool, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market")
            prompt = screen.session.interaction
            assert isinstance(prompt, InputInteraction)
            screen._output().append_activity(
                ActivityRecord(
                    activity_id="copy-during-prompt",
                    kind=ActivityKind.SYSTEM,
                    outcome=ActivityOutcome.NOTICE,
                    title="提示期间仍可复制",
                )
            )
            screen.submit("/copy 1")
            await pilot.pause()
            return (
                screen.session.interaction is prompt,
                app._clipboard,
                screen._input().has_focus,
            )

    prompt_preserved, clipboard, input_focused = asyncio.run(run())

    assert prompt_preserved
    assert "[A001] 提示期间仍可复制" in clipboard
    assert input_focused


def test_worker_busy_state_blocks_reentry_and_ctrl_c_restores_input() -> None:
    release = threading.Event()

    async def run() -> tuple[str, bool, RunningInteraction, str, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._start_operation(
                OperationSpec.create(
                    action_name="test.slow",
                    audit_summary="执行慢速测试任务",
                    route=ResultRoute(ResultKind.CONFIRMED),
                    operation=lambda: release.wait(2),
                    running_status="正在执行慢速测试任务",
                )
            )
            await pilot.pause(0.05)
            busy_mode = screen.session.interaction.mode.value
            running = screen.session.interaction
            assert isinstance(running, RunningInteraction)
            disabled = screen.query_one(
                "#command-input", WorkbenchCommandInput
            ).disabled
            screen.submit("/help")
            busy_output = _log_text(screen.query_one("#command-output", RichLog))
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            release.set()
            await pilot.pause()
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            return (
                busy_mode,
                disabled,
                running,
                screen.session.interaction.mode.value,
                command_input.has_focus and not command_input.disabled,
                busy_output,
            )

    busy_mode, disabled, running, restored_mode, usable, output = asyncio.run(run())
    assert busy_mode == "running"
    assert disabled
    assert running.cancellable
    assert restored_mode == "choice"
    assert usable
    assert "当前任务仍在运行" not in output


def test_idle_ctrl_c_requests_exit_confirmation_in_interaction_region() -> None:
    async def run() -> tuple[int | None, str, str, ConfirmInteraction, bool, int]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            status = str(screen.query_one("#command-status", Static).render())
            output = _log_text(screen.query_one("#command-output", RichLog))
            interaction = screen.session.interaction
            assert isinstance(interaction, ConfirmInteraction)
            actions = screen.query_one("#guided-actions", ActionList)
            focused = actions.has_focus
            highlighted = actions.highlighted
            await pilot.press("tab", "enter")
            await pilot.pause()
            return app.return_value, status, output, interaction, focused, highlighted

    return_value, status, output, interaction, focused, highlighted = asyncio.run(run())
    assert return_value == 0
    assert status == "等待确认"
    assert output == ""
    assert interaction.summary == "当前没有运行中的任务，是否退出？"
    assert interaction.force_hint is None
    assert focused
    assert highlighted == 0


def test_exit_confirmation_enter_defaults_to_cancel() -> None:
    async def run() -> tuple[int | None, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            return (
                app.return_value,
                screen.session.interaction.mode.value,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    return_value, mode, focused = asyncio.run(run())
    assert return_value is None
    assert mode == "choice"
    assert focused


def test_confirmation_mouse_click_only_selects_until_enter() -> None:
    async def run() -> tuple[int | None, str, int | None, list[str]]:
        executed: list[str] = []
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.request_confirmation("执行测试操作", lambda: executed.append("done"))
            await pilot.pause()
            actions = screen.query_one("#guided-actions", ActionList)

            await pilot.click(actions, offset=(4, 1))
            await pilot.pause()
            mode_after_click = screen.session.interaction.mode.value
            highlighted_after_click = actions.highlighted
            executed_after_click = list(executed)

            await pilot.press("enter")
            await pilot.pause()
            return (
                highlighted_after_click,
                mode_after_click,
                app.return_value,
                executed_after_click + executed,
            )

    highlighted, mode_after_click, return_value, execution_states = asyncio.run(run())

    assert highlighted == 1
    assert mode_after_click == "confirm"
    assert return_value is None
    assert execution_states == ["done"]


def test_ctrl_c_clears_non_empty_command_input_before_interrupting() -> None:
    async def run() -> tuple[str, str, str, int | None, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            command_input.value = "/help"
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                command_input.value,
                screen.session.interaction.mode.value,
                str(screen.query_one("#command-status", Static).render()),
                app.return_value,
                command_input.has_focus,
            )

    value, mode, status, return_value, focused = asyncio.run(run())
    assert value == ""
    assert mode == "choice"
    assert status == "已清空输入"
    assert return_value is None
    assert focused


def test_large_interaction_is_bounded_and_keeps_command_bar_visible() -> None:
    async def run() -> tuple[int, int, int, int, int, float, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.session.confirm(
                OperationSpec.create(
                    action_name="large operation",
                    audit_summary="large operation",
                    route=ResultRoute(ResultKind.CONFIRMED),
                    operation=lambda: None,
                    running_status="正在执行：large operation",
                ),
                title="需要确认",
                display_summary=Text(
                    "\n".join(f"preview line {index}" for index in range(30))
                ),
            )
            interaction = screen._interaction()
            interaction.present(screen.session.interaction)
            await pilot.pause()
            output = screen.query_one("#command-output", RichLog)
            command_bar = screen.query_one("#command-bar", Vertical)
            await pilot.press("alt+pagedown")
            await pilot.pause()
            return (
                interaction.region.height,
                interaction.virtual_size.height,
                output.region.height,
                command_bar.region.bottom,
                screen.region.height,
                interaction.scroll_y,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    (
        interaction_height,
        virtual_height,
        output_height,
        command_bottom,
        screen_height,
        scroll_y,
        input_focused,
    ) = asyncio.run(run())

    assert interaction_height <= screen_height * 0.4
    assert virtual_height > interaction_height
    assert output_height >= 5
    assert command_bottom <= screen_height
    assert scroll_y > 0
    assert input_focused


def test_output_paging_keeps_input_focus_and_ctrl_end_resumes_follow() -> None:
    async def run() -> tuple[float, int, bool, float, float, float, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(30):
                output.write(f"line {index}")
            await pilot.pause()
            initial_y = output.scroll_y
            initial_end = output.max_scroll_y
            await pilot.press("pageup")
            await pilot.pause()
            browsed_y = output.scroll_y
            focused_while_browsing = screen._input().has_focus
            await pilot.press("alt+down")
            await pilot.pause()
            line_scrolled_y = output.scroll_y
            await pilot.press("ctrl+end")
            await pilot.pause()
            return (
                initial_y,
                initial_end,
                focused_while_browsing,
                browsed_y,
                line_scrolled_y,
                output.scroll_y,
                screen._input().has_focus,
                str(screen.query_one("#command-hints", Static).render()),
            )

    (
        initial_y,
        initial_end,
        browsing_focus,
        browsed_y,
        line_scrolled_y,
        resumed_y,
        resumed_focus,
        hints,
    ) = asyncio.run(run())

    assert initial_y == initial_end
    assert browsed_y < initial_end
    assert line_scrolled_y > browsed_y
    assert browsing_focus
    assert resumed_y == initial_end
    assert resumed_focus
    assert hints == "Tab 聚焦选项  ·  ↑↓ 选择  ·  Enter 执行  ·  可输入编号"


def test_ctrl_c_cancels_idle_exit_confirmation() -> None:
    async def run() -> tuple[int | None, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                app.return_value,
                screen.session.interaction.mode.value,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    return_value, mode, focused = asyncio.run(run())
    assert return_value is None
    assert mode == "choice"
    assert focused


def test_invalid_input_keeps_idle_exit_confirmation_cancellable() -> None:
    async def run() -> tuple[int | None, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            screen.submit("account overview paper-main")
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                app.return_value,
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    return_value, output = asyncio.run(run())
    assert return_value is None
    assert "当前正在等待确认" not in output
    assert "已取消" not in output


def test_slash_exit_closes_workbench_even_while_argument_is_pending() -> None:
    async def run() -> int | None:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("1")
            assert isinstance(screen.session.interaction, InputInteraction)
            assert screen.session.interaction.action.feature is Feature.MARKET
            screen.submit("/exit")
        return app.return_value

    assert asyncio.run(run()) == 0


@pytest.mark.parametrize("command", ("exit", "quit", "q", "help", "back"))
def test_unsupported_bare_words_are_inline_errors_not_terminal_activities(
    command: str,
) -> None:
    async def run() -> tuple[int | None, str, int]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit(command)
            await pilot.pause(0.1)
            return (
                app.return_value,
                interaction_copy_text(screen.session.interaction),
                len(screen._output().activities),
            )

    return_value, interaction, activity_count = asyncio.run(run())
    assert return_value is None
    assert "高级命令请以 / 或 kairos 开头" in interaction
    assert activity_count == 0


def test_explicit_native_command_uses_owner_cli_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def run_native(
        application: object, component: str, arguments: list[str]
    ) -> dict[str, object]:
        seen.update(component=component, arguments=arguments)
        return {"schema": "risk-v1"}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.commands.NativeCliApplication.run",
        run_native,
    )

    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("kairos risk schema")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    output, focused = asyncio.run(run())
    assert seen == {"component": "risk", "arguments": ["standalone", "schema"]}
    assert "risk-v1" in output
    assert focused


def test_pasted_public_market_command_runs_in_workbench(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def run_market(application: object, arguments: list[str]) -> dict[str, object]:
        seen["arguments"] = arguments
        return {"schema": "market-v1"}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.commands.MarketCliApplication.run",
        run_market,
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            workspace = app.state.owner.paths.root
            screen.submit(
                "kairos market "
                f"--workspace {workspace} --format json "
                "standalone once --symbol AAPL"
            )
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
            )

    output, status = asyncio.run(run())

    assert seen == {"arguments": ["standalone", "once", "--symbol", "AAPL"]}
    assert "market-v1" in output
    assert status == "就绪"


def test_external_workbench_stylesheet_is_loaded_and_watchable() -> None:
    normal_app = KairosWorkbenchApp(_state())
    app = KairosWorkbenchApp(_state(), watch_css=True)

    assert [path.name for path in app.css_path] == ["workbench.tcss"]
    assert normal_app.theme == "kairos-nord"
    assert normal_app.css_monitor is None
    assert app.css_monitor is not None


def test_workbench_registers_and_selects_six_curated_themes() -> None:
    app = KairosWorkbenchApp(_state())

    expected = {
        "kairos-tokyo-night",
        "kairos-catppuccin",
        "kairos-nord",
        "kairos-gruvbox",
        "kairos-everforest",
        "kairos-dracula",
    }

    assert expected <= app.available_themes.keys()
    for alias, registered_name in (
        ("tokyo-night", "kairos-tokyo-night"),
        ("catppuccin", "kairos-catppuccin"),
        ("nord", "kairos-nord"),
        ("gruvbox", "kairos-gruvbox"),
        ("everforest", "kairos-everforest"),
        ("dracula", "kairos-dracula"),
    ):
        assert app.select_theme(alias)
        assert app.theme == registered_name

    assert not app.select_theme("unknown")


def test_theme_command_updates_css_and_rich_semantic_colors() -> None:
    async def run() -> tuple[str, str, str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/theme dracula")
            await pilot.pause()
            title = screen.query_one("#workspace-title", Static).render()
            assert isinstance(title, Content)
            styles = {str(span.style) for span in title.spans}
            theme = app.current_theme
            return (
                app.theme,
                theme.background,
                theme.panel,
                theme.primary,
                theme.foreground or "",
                "bold #bd93f9" in styles,
            )

    assert asyncio.run(run()) == (
        "kairos-dracula",
        "#282a36",
        "#44475a",
        "#bd93f9",
        "#f8f8f2",
        True,
    )


def test_theme_picker_uses_interaction_region_and_preserves_content() -> None:
    async def run() -> tuple[str, tuple[str, ...], str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = _log_text(screen.query_one("#command-output", RichLog))
            screen.submit("/theme")
            await pilot.pause()
            actions = screen.query_one("#guided-actions", ActionList)
            labels = tuple(item.label for item in actions.items)
            assert _log_text(screen.query_one("#command-output", RichLog)) == output
            screen.submit("1")
            await pilot.pause()
            return app.theme, labels, type(screen.session.interaction).__name__

    theme, labels, interaction = asyncio.run(run())

    assert theme == "kairos-tokyo-night"
    assert labels == (
        "Tokyo Night",
        "Catppuccin Mocha",
        "Nord  ✓",
        "Gruvbox Dark",
        "Everforest Dark",
        "Dracula",
    )
    assert interaction == "ChoiceInteraction"


@pytest.mark.parametrize("theme", ("textual-dark", "textual-light"))
def test_command_screen_renders_in_supported_terminal_themes(theme: str) -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            app.theme = theme
            await pilot.pause()
            return str(app.screen.query_one("#workspace-title", Static).render())

    assert asyncio.run(run()) == "KAIROS  ·  trader"


def test_command_screen_renders_when_no_color_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            return str(app.screen.query_one("#workspace-title", Static).render())

    assert asyncio.run(run()) == "KAIROS  ·  trader"


def test_workspace_identity_is_visible_in_shared_header_context() -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return app.screen.title or ""

    assert asyncio.run(run()) == "Kairos · trader"


def test_existing_setup_entry_starts_in_guided_product_context() -> None:
    async def run() -> tuple[type[object], str]:
        app = KairosWorkbenchApp(_state(), initial_section="resources")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            return type(app.screen), str(
                app.screen.query_one("#command-context", Static).render()
            )

    screen_type, context = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "trader › 连接与配置"


def test_command_input_executes_help_and_keeps_focus() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("slash", "h", "e", "l", "p", "enter")
            await pilot.pause()
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            return interaction_copy_text(
                screen.session.interaction
            ), command_input.has_focus

    output, input_focused = asyncio.run(run())
    assert "/market [代码]" in output
    assert input_focused


def test_market_command_guides_missing_argument_and_escape_cancels() -> None:
    async def run() -> tuple[str, str, InputInteraction, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("slash", "m", "a", "r", "k", "e", "t", "enter")
            await pilot.pause()
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            guided_placeholder = command_input.placeholder or ""
            guided_status = str(screen.query_one("#command-status", Static).render())
            interaction = screen.session.interaction
            assert isinstance(interaction, InputInteraction)
            await pilot.press("escape")
            await pilot.pause()
            ready_status = str(screen.query_one("#command-status", Static).render())
            return guided_placeholder, guided_status, interaction, ready_status

    placeholder, guided_status, interaction, ready_status = asyncio.run(run())

    assert placeholder == "输入代码或名称"
    assert guided_status == "搜索市场 · 等待输入"
    assert interaction.prompt == "输入代码或名称"
    assert ready_status == "就绪"


def test_command_input_keeps_explicit_history_shortcut() -> None:
    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("slash", "h", "e", "l", "p", "enter")
            await pilot.press("slash", "c", "l", "e", "a", "r", "enter")
            await pilot.press("ctrl+up")
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            latest = command_input.value
            await pilot.press("ctrl+up")
            previous = command_input.value
            return latest, previous

    assert asyncio.run(run()) == ("/clear", "/help")


def test_market_worker_error_keeps_search_prompt_usable_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: str) -> tuple[Market, ...]:
        raise RuntimeError("reference database unavailable")

    monkeypatch.setattr(
        market,
        "load_records",
        lambda *args, **kwargs: fail(str(args[2])),
    )

    async def run() -> tuple[str, str, bool, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
                command_input.has_focus,
                screen.session.interaction.mode.value,
                command_input.placeholder or "",
            )

    output, status, input_focused, prompt_mode, placeholder = asyncio.run(run())

    assert "reference database unavailable" in output
    assert status == "搜索市场失败 · 请重试"
    assert input_focused
    assert prompt_mode == "input"
    assert placeholder == "输入代码或名称"


def test_uninitialized_reference_catalog_offers_guided_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object, **__: object) -> tuple[Market, ...]:
        raise RuntimeError(
            "SQLite: read-only open failed (unable to open database file: "
            "/workspace/.kairos/state/reference/reference.sqlite); "
            "WAL sidecar open failed"
        )

    monkeypatch.setattr(market, "load_records", fail)

    async def run() -> tuple[tuple[str, ...], str, str, str | None]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            return (
                screen.session.context,
                interaction_copy_text(screen.session.interaction),
                str(screen.query_one("#command-status", Static).render()),
                screen.session.market.query,
            )

    context, interaction, status, query = asyncio.run(run())

    assert context == ("market", "missing")
    assert "还没有可查询的标的目录" in interaction
    assert "准备这个标的目录" in interaction
    assert "SQLite" not in interaction
    assert status == "标的目录尚未准备"
    assert query == "AAPL"


def test_command_layout_runs_at_supported_terminal_sizes() -> None:
    async def run(size: tuple[int, int]) -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return str(app.screen.query_one("#workspace-title", Static).render())

    for size in ((60, 20), (80, 24), (120, 30), (160, 40)):
        assert asyncio.run(run(size)) == "KAIROS  ·  trader"


def test_command_screen_applies_responsive_modes_during_terminal_resize() -> None:
    async def run() -> tuple[bool, bool, bool, bool, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            command_input.value = "/market AAPL"

            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            supported_narrow = screen.has_class("viewport-narrow")
            supported_short = screen.has_class("viewport-short")

            await pilot.resize_terminal(55, 16)
            await pilot.pause()
            warning = screen.query_one("#viewport-warning", Static)
            too_small = screen.has_class("viewport-too-small") and warning.display

            await pilot.resize_terminal(100, 30)
            await pilot.pause()
            restored = not screen.has_class("viewport-compact") and not warning.display
            return (
                supported_narrow,
                supported_short,
                too_small,
                restored,
                command_input.value,
                command_input.has_focus,
            )

    narrow, short, too_small, restored, value, focused = asyncio.run(run())

    assert narrow
    assert short
    assert too_small
    assert restored
    assert value == "/market AAPL"
    assert focused


def test_command_context_collapses_semantically_at_supported_widths() -> None:
    async def run() -> tuple[str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            await pilot.pause(0.1)
            wide = str(screen.query_one("#command-context", Static).render())
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            compact = str(screen.query_one("#command-context", Static).render())
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            narrow = str(screen.query_one("#command-context", Static).render())
            return wide, compact, narrow

    wide, compact, narrow = asyncio.run(run())

    assert wide == "trader › 运行中心 › 运行概览"
    assert compact == "trader › … › 运行概览"
    assert narrow == "trader › 运行概览"


def test_text_input_consumes_global_shortcuts_as_text() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            search = screen.query_one("#command-input", WorkbenchCommandInput)
            await pilot.press("q", "1", "?")
            await pilot.pause()
            return search.value, app.is_running

    value, running = asyncio.run(run())

    assert value == "q1?"
    assert running


def test_help_action_uses_interaction_region_without_opening_a_modal() -> None:
    async def run() -> tuple[bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            app.action_help()
            await pilot.pause()
            assert isinstance(screen, CommandLineScreen)
            content = interaction_copy_text(screen.session.interaction)
            return app.screen is screen, content

    stayed_inline, content = asyncio.run(run())

    assert stayed_inline
    assert "/help" in content
    assert "/market [代码]" in content
