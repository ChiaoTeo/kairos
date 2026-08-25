"""Single-screen guided command line for the Kairos Workbench."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Input, Static
from textual.worker import Worker

from kairospy.surface.presentation import (
    redact_cli_arguments,
    redact_text,
    redact_value,
)
from ..widgets import (
    ActionToken,
    ActivityStream,
    ChoiceInteraction,
    ConfirmInteraction,
    ControlInteraction,
    Feature,
    InputInteraction,
    InteractionRegion,
    InteractionState,
    RunningInteraction,
    WorkbenchCommandInput,
    WorkspaceHeader,
    interaction_copy_text,
    renderable_plain_text,
)
from .activity import ActivityKind, ActivityOutcome, ActivityRecord
from .effects import (
    AppendActivity,
    RefreshLaunchControl,
    RefreshMarketControl,
    RefreshOperationsLogs,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from . import flows as product_flows
from .flows.launch import runtime as launch_flow
from .flows.resources import configuration as resources_flow
from .flows.operations.observe_view import observe_renderable
from .navigation import (
    action_id,
    context_items,
    context_label,
    go_back,
    record_label,
)
from .operation import OperationSpec, RunningTask
from .results import ResultKind, ResultRoute

if TYPE_CHECKING:
    from ..app import KairosWorkbenchApp
from .catalog import HOME_ACTIONS, SECTION_ACTIONS
from .session import GuidedSession
from .commands import (
    is_dangerous as is_dangerous_kairos_command,
    preview as preview_kairos_command,
    run as run_kairos_command,
)


class CommandLineScreen(Screen[None]):
    """One output region and one stateful, guided command input."""

    TITLE = "Kairos"
    SUB_TITLE = "命令"
    BINDINGS = [
        Binding("ctrl+l", "clear", "清屏", show=False),
        Binding("escape", "back", "取消或返回", show=False),
        Binding("pageup", "scroll_output_up", "内容区向上翻页", show=False),
        Binding("pagedown", "scroll_output_down", "内容区向下翻页", show=False),
        Binding("alt+up", "scroll_output_line_up", "内容区向上滚动", show=False),
        Binding("alt+down", "scroll_output_line_down", "内容区向下滚动", show=False),
        Binding("ctrl+end", "follow_output", "回到底部并继续跟随", show=False),
        Binding("alt+pageup", "scroll_interaction_up", "交互区向上滚动", show=False),
        Binding(
            "alt+pagedown",
            "scroll_interaction_down",
            "交互区向下滚动",
            show=False,
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._interrupt_exit_pending = False
        self._running_task: RunningTask | None = None
        self._attach_refresh_worker: Worker[Any] | None = None
        self._market_refresh_worker: Worker[Any] | None = None
        self._operations_log_worker: Worker[Any] | None = None
        self.session = GuidedSession()

    @property
    def workbench_app(self) -> KairosWorkbenchApp:
        """Narrow Textual's generic App property to this Screen's owner."""

        return cast("KairosWorkbenchApp", self.app)

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield ActivityStream(
            id="command-output",
            wrap=True,
            highlight=False,
            markup=False,
        )
        yield Static(
            "本会话完成的操作和结果会保留在这里",
            id="activity-empty",
        )
        yield InteractionRegion(
            ChoiceInteraction(actions=HOME_ACTIONS),
            id="interaction-region",
        )
        with Horizontal(id="command-bar"):
            yield Static("首页  /", id="command-context")
            yield WorkbenchCommandInput(id="command-input")
        yield Static(
            "数字选择  ·  /back 返回  ·  /help 帮助  ·  /exit 退出\n"
            "Alt+↑↓ 滚动  ·  PgUp/PgDn 翻页  ·  Ctrl+End 最新",
            id="command-hints",
        )

    def on_mount(self) -> None:
        self._show_context()
        self.app.set_focus(self._input())
        self.set_interval(1.0, self._refresh_launch_attach)
        self.set_interval(1.0, self._refresh_operations_logs)
        self.set_interval(2.0, self._refresh_market_control)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        value = event.value.strip()
        interaction = self.session.interaction
        if not value and not isinstance(interaction, InputInteraction):
            return
        command_input = self._input()
        if not (isinstance(interaction, InputInteraction) and interaction.secret):
            command_input.remember(value)
        command_input.value = ""
        self.submit(value)

    def submit(self, value: str) -> None:
        """Execute input through the same path used by the visible prompt."""

        value = value.strip()
        interaction = self.session.interaction
        if not value and not isinstance(interaction, InputInteraction):
            return
        if self._running_task is not None:
            self._set_status("当前任务仍在运行 · Ctrl+C 取消")
            self.app.set_focus(self._input())
            return
        is_secret = isinstance(interaction, InputInteraction) and interaction.secret
        self.workbench_app.transcript.record(
            "input",
            screen=type(self).__name__,
            value="<redacted>" if is_secret else value,
            secret=is_secret,
        )
        if isinstance(interaction, ConfirmInteraction):
            command, arguments = _parse_command(value)
            is_allowed = value.startswith("/") and command in {
                "confirm",
                "cancel",
                "exit",
                "quit",
                "q",
                "help",
                "?",
            }
            if not is_allowed or arguments:
                self._interrupt_exit_pending = False
                self._set_status("等待确认 · 请输入 /confirm 或 /cancel")
                self.app.set_focus(self._input())
                return
            self._interrupt_exit_pending = False
            self._dispatch(command, arguments)
            self.app.set_focus(self._input())
            return
        if isinstance(interaction, InputInteraction):
            pending_command, pending_arguments = _parse_command(value)
            is_workbench_command = value.startswith("/")
            if (
                is_workbench_command
                and pending_command in {"exit", "quit", "q"}
                and not pending_arguments
            ):
                self.workbench_app.action_quit()
                return
            if (
                is_workbench_command
                and pending_command in {"back", "b"}
                and not pending_arguments
            ):
                self.action_back()
                self.app.set_focus(self._input())
                return
            if (
                is_workbench_command
                and pending_command == "cancel"
                and not pending_arguments
            ):
                self.action_cancel_pending()
                self.app.set_focus(self._input())
                return
            if (
                is_workbench_command
                and pending_command == "home"
                and not pending_arguments
            ):
                self.session.reset_prompt()
                self._input().password = False
                if is_secret:
                    self._cancel_input(interaction.action)
                self.session.home()
                self._show_context()
                return
            if (
                is_workbench_command
                and pending_command in {"help", "?"}
                and not pending_arguments
            ):
                self._interaction().present(self.session.interaction)
                self._set_status("仍在等待参数 · /back 取消")
                return
            pending = interaction.action
            self.session.finish_prompt()
            self._input().password = False
            self._input().placeholder = "输入命令；Enter 提交"
            self._dispatch_input(pending, value)
            self.app.set_focus(self._input())
            return
        if (
            not value.startswith("/")
            and not value.isdecimal()
            and self.session.context == ("resources", "setup")
            and value.lower() in {"feishu", "telegram"}
        ):
            self._dispatch(value.lower(), ())
            self.app.set_focus(self._input())
            return
        if not value.startswith("/") and not value.isdecimal():
            self._dispatch_kairos(value)
            return
        command, arguments = _parse_command(value)
        if command == "market":
            self.session.market.purpose = "search"
        self._dispatch(command, arguments)
        self.app.set_focus(self._input())

    def _dispatch_input(self, token: ActionToken, value: str) -> None:
        """Route a typed continuation to its owning product flow."""

        effects = product_flows.handle_input(
            self.workbench_app.state, self.session, token, value
        )
        if effects is None:
            self._set_status("输入上下文已经失效 · 请返回后重试")
            self._show_context()
            return
        self._apply_effects(effects)

    def _dispatch_kairos(self, value: str) -> None:
        try:
            argv = tuple(shlex.split(value))
        except ValueError as error:
            self._write_error(f"无法解析 kairos 命令：{error}")
            self._show_context()
            return
        if not argv:
            return
        equivalent = ("kairos", *argv)
        if argv in {("observe",), ("observe", "--once")}:
            self._start_operation(self._observe_spec())
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_kairos_command(argv)
            return run_kairos_command(self.workbench_app.state, argv)

        spec = OperationSpec.create(
            action_name="kairos.command",
            audit_summary=redact_text(shlex.join(equivalent)),
            route=ResultRoute(ResultKind.KAIROS_COMMAND),
            operation=operation,
            running_status=f"正在执行 {shlex.join(equivalent)}…",
            equivalent_command=equivalent,
        )
        if is_dangerous_kairos_command(argv) and not self.workbench_app.state.yes:
            self._present_confirmation(spec)
            return
        self._start_operation(spec)

    def _dispatch(self, command: str, arguments: tuple[str, ...]) -> None:
        if command in {"home", "/"}:
            self._finish_operations_logs()
            self.session.home()
            self._show_context()
        elif command in {"exit", "quit", "q"}:
            self._finish_operations_logs()
            self.workbench_app.action_quit()
        elif command in {"back", "b"}:
            self.action_back()
        elif command in {"help", "?"}:
            self._present_help()
        elif command == "clear":
            self.action_clear()
        elif command == "copy":
            self.workbench_app.action_copy_page()
        elif command == "copy-history":
            self.workbench_app.copy_current_page(history_only=True)
        elif command == "bottom":
            self._output().resume_follow()
            self._set_status("已回到最新活动")
        elif command == "transcript":
            self.session.choose(
                context_items(self.session, self.workbench_app.state),
                title=self._context_label(),
                summary=Text(
                    str(
                        self.workbench_app.transcript.path
                        or "Transcript 仅当前进程可用"
                    ),
                    style="dim",
                ),
            )
            self._interaction().present(self.session.interaction)
            self._set_status("Transcript 位置已显示")
        elif (
            effects := product_flows.handle_command(
                self.workbench_app.state,
                self.session,
                command,
                arguments,
            )
        ) is not None:
            self._apply_effects(effects)
        elif command == "observe":
            self._start_operation(self._observe_spec())
        elif command in {"confirm", "yes", "y"}:
            self._confirm_pending()
        elif command in {"cancel", "no", "n"}:
            self.action_cancel_pending()
        else:
            if self._dispatch_context(command, arguments):
                return
            self._write_error(f"未知操作：{command or value_or_unknown(arguments)}")
            self._show_context()

    def _dispatch_context(self, command: str, arguments: tuple[str, ...]) -> bool:
        if arguments:
            return False
        if not self.session.context:
            section = action_id(HOME_ACTIONS, command)
            if section is None:
                return False
            self.enter_section(section)
            return True

        effects = product_flows.handle_context(
            self.workbench_app.state,
            self.session,
            command,
        )
        if effects is None:
            return False
        self._apply_effects(effects)
        return True

    def enter_section(self, section: str) -> None:
        """Enter one product context without replacing the command screen."""

        if section == "observe":
            self._start_operation(self._observe_spec())
            return
        if section not in SECTION_ACTIONS:
            self._write_error(f"未知产品入口：{section}")
            return
        self.session.enter(section)
        self.workbench_app.transcript.record(
            "navigation", section=section, mode="guided"
        )
        self._show_context()

    def enter_launch_workflow(
        self, launch_id: str, action: str, source: Any | None = None
    ) -> None:
        """Translate launch CLI deep links into this screen's session context."""

        effects = launch_flow.enter_deep_link(
            self.workbench_app.state,
            self.session,
            launch_id,
            action,
            source,
        )
        self._apply_effects(effects)

    def action_back(self) -> None:
        if isinstance(self.session.interaction, (InputInteraction, ConfirmInteraction)):
            interaction = self.session.interaction
            token = (
                interaction.action
                if isinstance(interaction, InputInteraction)
                else None
            )
            if (
                isinstance(interaction, ConfirmInteraction)
                and interaction.operation.route.kind is ResultKind.RESOURCE_WIZARD
            ):
                token = ActionToken(Feature.RESOURCES, "resource:setup")
            effects = (
                resources_flow.back_wizard(
                    self.workbench_app.state, self.session, token
                )
                if token is not None
                else None
            )
            if effects is not None:
                self._input().password = False
                self._apply_effects(effects)
                return
            self.action_cancel_pending()
            return
        if (
            self.session.context == ("resources", "setup")
            and self.session.resources.wizard is not None
        ):
            resources_flow.cancel_input(
                self.session,
                ActionToken(Feature.RESOURCES, "resource:setup"),
            )
            self.session.reset_prompt()
            self._show_context()
            return
        if self.session.context[:2] == ("operations", "service-logs"):
            self._finish_operations_logs()
        if not go_back(self.session):
            self._show_context()
            self._set_status("当前已经在首页")
            return
        self._show_context()

    def action_clear(self) -> None:
        self._output().clear_visible_history()
        self.query_one("#activity-empty", Static).display = True
        self._show_context()
        self._set_status("活动记录已清空 · Transcript 和业务状态未改变")

    def copy_page_text(self, *, history_only: bool = False) -> str:
        """Build the redacted handoff view from state and visible activities."""

        activity_text = self._output().plain_text
        if history_only:
            return activity_text
        sections = [
            f"Workspace: {self.workbench_app.state.workspace_id}",
            f"Context: {context_label(self.session.context)}",
        ]
        interaction_text = interaction_copy_text(self.session.interaction)
        if interaction_text:
            sections.append(f"## 当前交互\n{interaction_text}")
        if activity_text:
            sections.append(f"## 活动记录\n{activity_text}")
        return redact_text("\n\n".join(sections))

    def _present_help(self) -> None:
        context = self._context_label()
        self.session.choose(
            context_items(self.session, self.workbench_app.state),
            title=f"{context} · 帮助",
            summary=_help_table(self.session.context),
        )
        self._interaction().present(self.session.interaction)
        self._set_status("帮助 · 选择动作或输入 /back 返回")

    def action_scroll_interaction_up(self) -> None:
        """Page the bounded interaction region without moving input focus."""

        self._interaction().scroll_page_up(animate=False)

    def action_scroll_interaction_down(self) -> None:
        """Page the bounded interaction region without moving input focus."""

        self._interaction().scroll_page_down(animate=False)

    def action_scroll_output_up(self) -> None:
        """Browse older output while leaving command input ownership unchanged."""

        output = self._output()
        output.pause_follow()
        if self.session.context[:2] == ("operations", "service-logs"):
            buffer = self.session.operations.live_buffer
            if buffer is not None:
                buffer.pause()
        output.scroll_page_up(animate=False)

    def action_scroll_output_down(self) -> None:
        """Browse newer output while leaving command input ownership unchanged."""

        self._output().scroll_page_down(animate=False)

    def action_scroll_output_line_up(self) -> None:
        """Move one line toward older output without moving input focus."""

        output = self._output()
        output.pause_follow()
        if self.session.context[:2] == ("operations", "service-logs"):
            buffer = self.session.operations.live_buffer
            if buffer is not None:
                buffer.pause()
        output.scroll_up(animate=False)

    def action_scroll_output_line_down(self) -> None:
        """Move one line toward newer output without moving input focus."""

        self._output().scroll_down(animate=False)

    def action_follow_output(self) -> None:
        """Return to the newest output and resume automatic following."""

        self._output().resume_follow()
        if self.session.context[:2] == ("operations", "service-logs"):
            buffer = self.session.operations.live_buffer
            if buffer is not None:
                buffer.resume()
                self._apply_effects(
                    product_flows.operations.log_control_effects(
                        self.session, "实时日志继续跟随"
                    )
                )

    def action_cancel_pending(self) -> None:
        interaction = self.session.interaction
        if isinstance(interaction, InputInteraction):
            self._input().password = False
            self._cancel_input(interaction.action)
            self.session.reset_prompt()
            self._input().placeholder = "输入命令；Enter 提交"
            self._set_status("就绪")
            self._show_context()
            return
        if isinstance(interaction, ConfirmInteraction):
            self._interrupt_exit_pending = False
            product_flows.cancel_confirmation(self.session, interaction.operation)
            self.session.reset_prompt()
            self._set_status("就绪")
            self._show_context()
            return
        if (
            self.session.context == ("resources", "setup")
            and self.session.resources.wizard is not None
        ):
            resources_flow.cancel_input(
                self.session,
                ActionToken(Feature.RESOURCES, "resource:setup"),
            )
            self.session.reset_prompt()
            self._set_status("向导已取消")
            self._show_context()
            return
        cancelled = self.workers.cancel_node(self)
        if cancelled:
            self._set_status(f"已请求取消 {len(cancelled)} 个当前任务")
            self.app.set_focus(self._input())
            return
        self._show_context()
        self._set_status("当前没有可取消的输入或任务")

    def _cancel_input(self, token: ActionToken) -> None:
        product_flows.cancel_input(self.session, token)

    def action_interrupt(self) -> None:
        """Cancel active work, or ask before exiting when completely idle."""

        if self._interrupt_exit_pending:
            self.workbench_app.transcript.record(
                "session_finished", status="forced_interrupt"
            )
            self.app.exit(130)
            return
        if (
            isinstance(self.session.interaction, (InputInteraction, ConfirmInteraction))
            or self._running_task is not None
        ):
            self.action_cancel_pending()
            return
        self.request_confirmation(
            "当前没有运行中的任务，是否退出？",
            self.workbench_app.action_quit,
            route=ResultRoute(ResultKind.CONFIRMED, "exit"),
            title="退出 Workbench",
            force_hint="再次按 Ctrl+C 强制退出，返回码 130。",
        )
        self._interrupt_exit_pending = True

    def request_confirmation(
        self,
        summary: str,
        action: Callable[[], Any],
        *,
        route: ResultRoute = ResultRoute(ResultKind.CONFIRMED),
        title: str = "需要确认",
        details: RenderableType | None = None,
        force_hint: str | None = None,
    ) -> None:
        """Stage a dangerous action in the interaction region, without a modal."""

        self._interrupt_exit_pending = False
        operation = OperationSpec.create(
            action_name=("workbench.exit" if route.qualifier == "exit" else summary),
            audit_summary=redact_text(summary),
            route=route,
            operation=action,
            running_status=f"正在执行：{summary}",
        )
        self._present_confirmation(
            operation,
            title=title,
            details=details,
            force_hint=force_hint,
        )

    def _present_confirmation(
        self,
        operation: OperationSpec,
        *,
        title: str = "需要确认",
        details: RenderableType | None = None,
        force_hint: str | None = None,
    ) -> None:
        """Present the immutable operation which acceptance will start."""

        self.session.confirm(
            operation,
            title=title,
            display_summary=details,
            force_hint=force_hint,
        )
        self.workbench_app.transcript.record(
            "confirmation_requested",
            screen=type(self).__name__,
            operation_id=operation.operation_id,
            summary=operation.audit_summary,
        )
        self._interaction().present(self.session.interaction)
        self._input().placeholder = "输入 /confirm 或 /cancel"
        self._set_hints("/confirm 继续  ·  /cancel 或 Esc 取消")
        self._set_status("等待确认")

    def _confirm_pending(self) -> None:
        interaction = self.session.interaction
        if not isinstance(interaction, ConfirmInteraction):
            self._set_status("当前没有等待确认的操作")
            return
        operation = interaction.operation
        summary = operation.audit_summary
        self._interrupt_exit_pending = False
        self.session.finish_prompt()
        self.workbench_app.transcript.record(
            "confirmation_accepted",
            screen=type(self).__name__,
            operation_id=operation.operation_id,
            summary=summary,
        )
        if operation.route == ResultRoute(ResultKind.CONFIRMED, "exit"):
            operation.operation()
            return
        self._start_operation(operation)

    def _start_operation(self, spec: OperationSpec) -> None:
        equivalent = spec.equivalent_command
        safe_equivalent = redact_cli_arguments(equivalent) if equivalent else None
        arguments = (
            safe_equivalent[1:]
            if safe_equivalent and safe_equivalent[:1] == ("kairos",)
            else ()
        )
        if self.workbench_app.transcript.claim_operation(spec.operation_id):
            self.workbench_app.transcript.record(
                "action",
                screen=type(self).__name__,
                operation_id=spec.operation_id,
                action=spec.action_name,
                display=spec.audit_summary,
                arguments=list(arguments),
                equivalent_command=(
                    shlex.join(safe_equivalent) if safe_equivalent else None
                ),
            )
            self.workbench_app.transcript.record(
                "operation_started",
                operation_id=spec.operation_id,
                action=spec.action_name,
                summary=spec.audit_summary,
                route=spec.route.kind.value,
                qualifier=spec.route.qualifier,
            )
        self.session.busy(spec.route, message=spec.running_status)
        self._interaction().present(self.session.interaction)
        self._input().disabled = True
        self._set_status(spec.running_status)
        worker = self.run_worker(
            spec.operation,
            name=f"command-{spec.route.kind.value}",
            group="guided-command",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        self._running_task = RunningTask(spec, worker)

    def _apply_effects(
        self,
        effects: tuple[ScreenEffect, ...],
        operation: OperationSpec | None = None,
    ) -> None:
        for effect in effects:
            if isinstance(effect, AppendActivity):
                activity = effect.activity
                if (
                    operation is not None
                    and activity.activity_id == operation.operation_id
                    and activity.equivalent_command is None
                ):
                    activity = replace(
                        activity,
                        equivalent_command=operation.equivalent_command,
                    )
                self._output().append_activity(activity)
                self.query_one("#activity-empty", Static).display = False
            elif isinstance(effect, SetInteraction):
                self.session.interaction = effect.interaction
                if isinstance(effect.interaction, ConfirmInteraction):
                    self.workbench_app.transcript.record(
                        "confirmation_requested",
                        screen=type(self).__name__,
                        summary=effect.interaction.operation.audit_summary,
                    )
                self._interaction().present(effect.interaction)
                self._sync_input_to_interaction(effect.interaction)
                self._sync_context_chrome()
            elif isinstance(effect, RunOperation):
                self._start_operation(effect.operation)
            elif isinstance(effect, SetStatus):
                self._set_status(effect.message)
            elif isinstance(effect, RefreshMarketControl):
                self._refresh_market_control(force=effect.force)
            elif isinstance(effect, RefreshLaunchControl):
                self._present_launch_control()
                self._sync_input_to_interaction(self.session.interaction)
                self._sync_context_chrome()
                self._refresh_launch_attach(force=effect.force)
            elif isinstance(effect, RefreshOperationsLogs):
                component = self.session.operations.selected_service or "服务"
                output = self._output()
                if effect.reset_view:
                    if output.live_title is None:
                        output.begin_live_stream(f"{component} 日志 · 跟随中")
                    else:
                        output.clear_live_stream()
                    self.query_one("#activity-empty", Static).display = False
                buffer = self.session.operations.live_buffer
                if buffer is not None and buffer.following:
                    output.resume_follow()
                else:
                    output.pause_follow()
                if effect.force:
                    self._refresh_operations_logs(force=True)
        self._report_unseen_activity()

    def _report_unseen_activity(self) -> None:
        unseen = self._output().new_activity_count
        if unseen:
            self._set_status(f"有 {unseen} 条新活动 · Ctrl+End 查看")

    def _sync_input_to_interaction(self, interaction: InteractionState) -> None:
        command_input = self._input()
        command_input.disabled = isinstance(interaction, RunningInteraction)
        command_input.password = (
            isinstance(interaction, InputInteraction) and interaction.secret
        )
        if isinstance(interaction, InputInteraction):
            command_input.placeholder = interaction.prompt
        elif isinstance(interaction, ConfirmInteraction):
            command_input.placeholder = "输入 /confirm 或 /cancel"
        else:
            command_input.placeholder = "输入编号或命令；Enter 提交"
        if not command_input.disabled:
            self.app.set_focus(command_input)

    def _sync_context_chrome(self) -> None:
        interaction = self.session.interaction
        context = (
            interaction.title
            if isinstance(interaction, InputInteraction) and interaction.title
            else self._context_label()
        )
        self.query_one("#command-context", Static).update(f"{context}  ›")
        if isinstance(interaction, InputInteraction):
            verb = (
                "搜索"
                if interaction.action == ActionToken(Feature.MARKET, "search")
                and self.session.market.purpose == "search"
                else "确认"
            )
            self._set_hints(f"Enter {verb}  ·  Esc 返回")
            return
        if len(self.session.context) > 1 and self.session.visible_records:
            self.query_one("#command-hints", Static).update(
                "输入结果编号查看详情  ·  Esc 返回"
            )
        else:
            self.query_one("#command-hints", Static).update(
                "数字选择  ·  /back 返回  ·  /help 更多操作  ·  /exit 退出"
            )

    def _read_observe(self) -> object | None:
        return self.workbench_app.state.refresh_snapshot()

    def _observe_command(self) -> tuple[str, ...]:
        state = self.workbench_app.state
        command = ["kairos", "observe"]
        if state.workspace_arg is not None:
            command.extend(("--workspace", str(state.workspace_arg)))
        command.append("--once")
        return tuple(command)

    def _observe_spec(self) -> OperationSpec:
        return OperationSpec.create(
            action_name="system.observe",
            audit_summary="刷新系统状态",
            route=ResultRoute(ResultKind.OBSERVE),
            operation=self._read_observe,
            running_status=_running_status(ResultKind.OBSERVE),
            equivalent_command=self._observe_command(),
        )

    def _refresh_market_control(self, *, force: bool = False) -> None:
        if self.session.context != ("market", "selected"):
            return
        if not force and not self.session.market.refresh_enabled:
            return
        if self._market_refresh_worker is not None:
            return
        operation = product_flows.market.live_observation_operation(
            self.workbench_app.state, self.session
        )
        if operation is None:
            return
        self._market_refresh_worker = self.run_worker(
            operation,
            name="market-control-stream",
            group="market-control-stream",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _present_market_control(self) -> None:
        if self.session.context != ("market", "selected"):
            return
        market = self.session.market.selected
        snapshot = self.session.market.snapshot
        if market is None or snapshot is None:
            return
        self.session.control(
            product_flows.market.selected_title(self.session) or record_label(market),
            snapshot,
            context_items(self.session, self.workbench_app.state),
            refreshing=self.session.market.refresh_enabled,
        )
        self._interaction().present(self.session.interaction)

    def _refresh_launch_attach(self, *, force: bool = False) -> None:
        if self.session.context != ("strategy", "attach"):
            return
        if self._attach_refresh_worker is not None:
            return
        operation = launch_flow.attach_operation(self.workbench_app.state, self.session)
        if operation is None:
            return
        self._attach_refresh_worker = self.run_worker(
            operation,
            name="launch-attach-stream",
            group="launch-attach-stream",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _refresh_operations_logs(self, *, force: bool = False) -> None:
        if self.session.context[:2] != ("operations", "service-logs"):
            return
        if self._operations_log_worker is not None:
            return
        operation = product_flows.operations.service_log_operation(
            self.workbench_app.state, self.session
        )
        if operation is None:
            return
        self._operations_log_worker = self.run_worker(
            operation,
            name="operations-service-logs",
            group="operations-service-logs",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _render_operations_logs(self, result: Any) -> None:
        if self.session.context[:2] != ("operations", "service-logs"):
            return
        buffer = self.session.operations.live_buffer
        if buffer is None:
            return
        raw_lines = result.get("lines", ()) if isinstance(result, Mapping) else ()
        lines = tuple(str(line) for line in raw_lines)
        generation = (
            str(result["generation"])
            if isinstance(result, Mapping) and result.get("generation") is not None
            else None
        )
        byte_size = int(result.get("size") or 0) if isinstance(result, Mapping) else 0
        rotated = (
            self.session.operations.log_generation is not None
            and generation is not None
            and generation != self.session.operations.log_generation
        )
        truncated = byte_size < self.session.operations.log_size
        seen = () if rotated or truncated else self.session.operations.source_tail
        overlap = 0
        for size in range(min(len(seen), len(lines)), 0, -1):
            if seen[-size:] == lines[:size]:
                overlap = size
                break
        new_lines = lines[overlap:]
        status = self.session.operations.selected_service_status
        if isinstance(result, Mapping) and result.get("path"):
            buffer.full_log_path = Path(str(result["path"]))
        elif status is not None and status.raw.get("log_file"):
            buffer.full_log_path = Path(str(status.raw["log_file"]))
        buffer.extend(new_lines)
        self.session.operations.source_tail = lines[-buffer.capacity :]
        self.session.operations.log_generation = generation
        self.session.operations.log_size = byte_size
        self.session.operations.received_lines += len(new_lines)
        self.session.operations.warning_lines += sum(
            1 for line in new_lines if "warn" in line.lower() or "error" in line.lower()
        )
        output = self._output()
        if output.live_title is None:
            component = self.session.operations.selected_service or "服务"
            output.begin_live_stream(f"{component} 日志 · 跟随中")
        output.append_live_lines(new_lines, retained_lines=tuple(buffer.lines))
        self._apply_effects(
            product_flows.operations.log_control_effects(
                self.session,
                "实时日志 · 后台刷新中"
                if buffer.following
                else f"实时日志已暂停 · {buffer.unseen_lines} 行未读",
            )
        )

    def _finish_operations_logs(self) -> None:
        if self._operations_log_worker is not None:
            self._operations_log_worker.cancel()
            self._operations_log_worker = None
        activity = product_flows.operations.finish_log_follow(self.session)
        self._output().end_live_stream()
        if activity is not None:
            self._apply_effects((activity,))

    def _render_launch_attach_snapshot(self, result: Any) -> None:
        if not isinstance(result, Mapping):
            self.session.strategy.attach_snapshot = Pretty(result, expand_all=True)
        else:
            runtime = result.get("status")
            instance = result.get("instance")
            self.session.strategy.attach_snapshot = Pretty(
                {"instance": instance, "status": runtime}, expand_all=True
            )
        self._present_launch_control()
        if not isinstance(result, Mapping):
            return
        logs = result.get("logs")
        raw_lines = logs.get("lines", ()) if isinstance(logs, Mapping) else ()
        lines = tuple(str(line) for line in raw_lines)
        seen = self.session.strategy.source_tail
        overlap = 0
        for size in range(min(len(seen), len(lines)), 0, -1):
            if seen[-size:] == lines[:size]:
                overlap = size
                break
        live_buffer = self.session.strategy.live_buffer
        if live_buffer is None:
            self.session.strategy.reset_live_buffer("launch-attach")
            live_buffer = self.session.strategy.live_buffer
        assert live_buffer is not None
        if isinstance(logs, Mapping):
            full_log = logs.get("latest") or logs.get("path")
            if full_log:
                live_buffer.full_log_path = Path(str(full_log))
        live_buffer.extend(lines[overlap:])
        self.session.strategy.source_tail = lines[-live_buffer.capacity :]
        self._present_launch_control()

    def _present_launch_control(self) -> None:
        if self.session.context != ("strategy", "attach"):
            return
        record = self.session.strategy.selected_record or {}
        launch_id = str(record.get("launch_id") or "Launch")
        status_snapshot = self.session.strategy.attach_snapshot or Text(
            "等待首次运行状态…", style="dim"
        )
        live_buffer = self.session.strategy.live_buffer
        tail = (
            Text("\n".join(live_buffer.lines))
            if live_buffer is not None and live_buffer.lines
            else None
        )
        live_status = (
            Text(
                f"可见 {len(live_buffer.lines)} 行"
                f" · 未读 {live_buffer.unseen_lines}"
                f" · 已丢弃 {live_buffer.dropped_lines}",
                style="dim",
            )
            if live_buffer is not None
            else None
        )
        full_log = (
            Text(f"完整日志 {live_buffer.full_log_path}", style="dim")
            if live_buffer is not None and live_buffer.full_log_path is not None
            else Text(
                "完整日志 当前数据源未提供路径 · /copy 仅复制当前窗口", style="dim"
            )
        )
        snapshot = Group(
            *(
                part
                for part in (status_snapshot, tail, live_status, full_log)
                if part is not None
            )
        )
        self.session.control(
            f"Launch {launch_id}",
            snapshot,
            launch_flow.ATTACH_ACTIONS,
            refreshing=not self.session.strategy.attach_paused,
        )
        self._interaction().present(self.session.interaction)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "market-control-stream":
            if event.worker is not self._market_refresh_worker:
                return
            self._market_refresh_worker = None
            if self.session.context != ("market", "selected"):
                return
            if event.state.name == "SUCCESS":
                self.session.market.snapshot = (
                    product_flows.market.observation_renderable(event.worker.result)
                )
                if self.session.market.refresh_enabled:
                    self._present_market_control()
                else:
                    self._show_context()
                self._set_status(
                    "行情自动刷新中"
                    if self.session.market.refresh_enabled
                    else "行情已刷新"
                )
            elif event.state.name == "ERROR":
                self.session.market.refresh_enabled = False
                self._write_error(str(event.worker.error))
                self._show_context()
                self._set_status("行情刷新失败 · 已暂停")
            return
        if event.worker.group == "launch-attach-stream":
            if event.worker is not self._attach_refresh_worker:
                return
            if event.state.name == "SUCCESS":
                self._attach_refresh_worker = None
                if self.session.context == ("strategy", "attach"):
                    self._render_launch_attach_snapshot(event.worker.result)
                    self._set_status("跟随输出 · 后台刷新中")
            elif event.state.name == "ERROR":
                self._attach_refresh_worker = None
                self.session.strategy.attach_paused = True
                if self.session.context == ("strategy", "attach"):
                    self._write_error(str(event.worker.error))
                    self.session.strategy.attach_snapshot = Text(
                        "刷新失败；可重试、继续或返回。", style="yellow"
                    )
                    self._present_launch_control()
                    self._set_status("跟随输出失败 · 已暂停")
            elif event.state.name == "CANCELLED":
                self._attach_refresh_worker = None
            return
        if event.worker.group == "operations-service-logs":
            if event.worker is not self._operations_log_worker:
                return
            if event.state.name == "SUCCESS":
                self._operations_log_worker = None
                self._render_operations_logs(event.worker.result)
            elif event.state.name == "ERROR":
                self._operations_log_worker = None
                if self.session.context[:2] == ("operations", "service-logs"):
                    self._write_error(str(event.worker.error))
                    self._set_status("实时日志刷新失败 · 可重试或返回")
            elif event.state.name == "CANCELLED":
                self._operations_log_worker = None
            return
        if event.worker.group != "guided-command":
            return
        running_task = self._running_task
        if running_task is None or event.worker is not running_task.worker:
            return
        route = running_task.spec.route
        if event.state.name == "SUCCESS":
            self._set_status("就绪")
            self._running_task = None
            self._restore_navigation_input()
            effects = product_flows.handle_success(
                self.workbench_app.state,
                self.session,
                running_task.spec,
                event.worker.result,
            )
            if effects is not None:
                self._apply_effects(effects, running_task.spec)
                return
            self._append_terminal_activity(
                running_task.spec,
                ActivityOutcome.SUCCESS,
                body=_shell_result_body(route.kind, event.worker.result),
            )
            self._show_context()
            self._report_unseen_activity()
        elif event.state.name == "ERROR":
            error = str(event.worker.error)
            self._running_task = None
            self._restore_navigation_input()
            effects = product_flows.handle_failure(
                self.workbench_app.state,
                self.session,
                running_task.spec,
                error,
            )
            if effects is not None:
                self._apply_effects(effects, running_task.spec)
            else:
                self._append_terminal_activity(
                    running_task.spec,
                    ActivityOutcome.FAILURE,
                    body=Text(error, style="red"),
                    copy_text=error,
                )
                self._show_context()
                self._set_status("操作失败 · 可重试、返回或查看帮助")
                self._report_unseen_activity()
        elif event.state.name == "CANCELLED":
            self._set_status("已取消 · 可继续输入")
            self._running_task = None
            self._restore_navigation_input()
            effects = product_flows.handle_cancel(
                self.workbench_app.state,
                self.session,
                running_task.spec,
            )
            if effects is not None:
                self._apply_effects(effects, running_task.spec)
            else:
                self._append_terminal_activity(
                    running_task.spec,
                    ActivityOutcome.CANCELLED,
                    body=Text("操作在开始执行后被取消。", style="yellow"),
                    copy_text="操作在开始执行后被取消。",
                )
                self._show_context()
                self._set_status("操作已取消 · 可继续输入")
                self._report_unseen_activity()

    def _restore_navigation_input(self) -> None:
        self.session.finish_prompt()
        self._input().disabled = False
        self._input().password = False
        self.app.set_focus(self._input())
        self.call_after_refresh(self.app.set_focus, self._input())

    def _show_context(self) -> None:
        if isinstance(
            self.session.interaction, (ChoiceInteraction, ControlInteraction)
        ):
            self._input().disabled = False
        items = context_items(self.session, self.workbench_app.state)
        context = self._context_label()
        if (
            self.session.context == ("market", "selected")
            and self.session.market.snapshot is not None
            and self.session.market.refresh_enabled
        ):
            self._present_market_control()
        elif self.session.context == ("strategy", "attach"):
            self._present_launch_control()
        else:
            self.session.choose(items, title=context)
            self._interaction().present(self.session.interaction)
        self.query_one("#command-context", Static).update(f"{context}  ›")
        self._input().placeholder = "输入编号或命令；Enter 提交"
        empty_resource_label = resources_flow.empty_resource_label(self.session)
        if empty_resource_label is not None:
            self._set_hints("输入 /new 开始配置  ·  Esc 返回")
        else:
            self._set_hints("数字选择  ·  /back 返回  ·  /help 更多操作  ·  /exit 退出")
        if (
            self.session.context == ("market", "selected")
            and self.session.market.snapshot is not None
        ):
            self._set_status(
                "行情自动刷新中"
                if self.session.market.refresh_enabled
                else "行情已就绪"
            )
        elif self.session.context == ("strategy", "attach"):
            self._set_status(
                "跟随输出 · 已暂停"
                if self.session.strategy.attach_paused
                else "跟随输出 · 后台刷新中"
            )
        elif empty_resource_label is not None:
            self._set_status(f"尚未配置 {empty_resource_label}")
        else:
            self._set_status("就绪")
        self.app.set_focus(self._input())
        self.call_after_refresh(self.app.set_focus, self._input())

    def _context_label(self) -> str:
        context = context_label(self.session.context)
        if self.session.context == ("market", "selected"):
            market = self.session.market.selected
            if market is not None:
                title = product_flows.market.selected_title(self.session)
                return f"{context} · {title or record_label(market)}"
        if self.session.context[:1] == ("resources",):
            return resources_flow.context_title(self.session, context)
        return context

    def _write_error(self, value: str) -> None:
        if self.session.reject_input(value):
            self._interaction().present(self.session.interaction)
            self._set_status("输入有误 · 请修正")
            return
        self.session.choose(
            context_items(self.session, self.workbench_app.state),
            title=self._context_label(),
            summary=Text(value, style="red"),
        )
        self._interaction().present(self.session.interaction)
        self._sync_context_chrome()
        self._set_status("命令失败 · 可返回或查看帮助")

    def _append_terminal_activity(
        self,
        spec: OperationSpec,
        outcome: ActivityOutcome,
        *,
        body: RenderableType,
        copy_text: str | None = None,
    ) -> None:
        rendered_text = copy_text or renderable_plain_text(body)
        self.query_one("#activity-empty", Static).display = False
        self._output().append_activity(
            ActivityRecord(
                activity_id=spec.operation_id,
                kind=_activity_kind(spec.route.kind),
                outcome=outcome,
                title=spec.audit_summary,
                body=body,
                copy_text=redact_text(rendered_text),
                audit_summary=spec.audit_summary,
                equivalent_command=spec.equivalent_command,
            )
        )

    def _output(self) -> ActivityStream:
        return self.query_one("#command-output", ActivityStream)

    def _input(self) -> WorkbenchCommandInput:
        return self.query_one("#command-input", WorkbenchCommandInput)

    def _interaction(self) -> InteractionRegion:
        return self.query_one("#interaction-region", InteractionRegion)

    def _set_status(self, value: str) -> None:
        self.query_one(WorkspaceHeader).set_status(value)

    def _set_hints(self, primary: str) -> None:
        self.query_one("#command-hints", Static).update(
            f"{primary}\nAlt+↑↓ 滚动  ·  PgUp/PgDn 翻页  ·  Ctrl+End 最新"
        )


def _parse_command(value: str) -> tuple[str, tuple[str, ...]]:
    stripped = value.strip().removeprefix("/")
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return "", ()
    if not parts:
        return "help", ()
    return parts[0].lower(), tuple(parts[1:])


def value_or_unknown(arguments: tuple[str, ...]) -> str:
    return " ".join(arguments) or "<无法解析>"


def _activity_kind(kind: ResultKind) -> ActivityKind:
    if kind in {
        ResultKind.OBSERVE,
        ResultKind.MARKET,
        ResultKind.MARKET_ROUTES,
        ResultKind.MARKET_OBSERVATION,
        ResultKind.MARKET_DATASETS,
        ResultKind.REFERENCE_RECORDS,
        ResultKind.REFERENCE_RELATED,
        ResultKind.RESOURCES_SUMMARY,
        ResultKind.RESOURCE_LIST,
        ResultKind.OPERATIONS_SERVICES,
        ResultKind.STRATEGY_LAUNCHES,
        ResultKind.STRATEGY_INSTANCES,
        ResultKind.STRATEGY_COMPONENTS,
        ResultKind.STRATEGY_INSTANCE,
        ResultKind.STRATEGY_TIMELINE,
    }:
        return ActivityKind.QUERY
    if kind is ResultKind.STRATEGY_TIMELINE_EXPORT:
        return ActivityKind.ARTIFACT
    return ActivityKind.OPERATION


def _shell_result_body(kind: ResultKind, result: Any) -> RenderableType:
    """Render the small set of non-product operations owned by the shell."""

    if kind is ResultKind.OBSERVE:
        return (
            Text("当前没有可用的系统观察结果。", style="dim")
            if result is None
            else observe_renderable(result)
        )
    if kind is ResultKind.KAIROS_COMMAND:
        return Panel(
            Pretty(_redact_result(result), expand_all=True), title="kairos 命令结果"
        )
    return Panel(str(result), title="完成", border_style="green")


def _redact_result(value: Any) -> Any:
    """Redact nested shell results before they become a visible Rich renderable."""

    if isinstance(value, Mapping):
        redacted = redact_value(value)
        command = value.get("command")
        if isinstance(command, (list, tuple)):
            redacted["command"] = list(
                redact_cli_arguments(tuple(str(part) for part in command))
            )
        return redacted
    return redact_value(value)


def _help_table(context: tuple[str, ...] = ()) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("编号 / 动作名", "执行当前上方列出的操作")
    table.add_row("其他文本", "作为 kairos <输入> 交给所属 Application 执行")
    table.add_row("/back", "返回上一级；等待参数时取消当前步骤")
    table.add_row("/home", "返回首页")
    table.add_row("/exit", "退出 Kairos Workbench")
    table.add_row("/observe", "查看 Workspace 组件和 Launch 状态")
    table.add_row("/market [代码]", "搜索有效市场标的；省略代码时进入引导")
    if context[:1] == ("market",):
        table.add_row("/r", "回放本地 JSONL 行情")
        table.add_row("/c", "连接运行中的行情服务")
        table.add_row("/d", "诊断市场定义和 Reference 映射")
        table.add_row("/a", "输入完整 Market ID")
    table.add_row("/clear", "清空当前输出显示")
    table.add_row("/transcript", "显示当前 Agent 可读会话记录的路径")
    table.add_row("/copy", "复制当前页完整输出，可直接粘贴给 Agent")
    table.add_row("/copy-history", "只复制当前会话的活动记录")
    table.add_row("/bottom", "回到最新活动并恢复自动跟随")
    table.add_row("/confirm /cancel", "继续或取消等待中的步骤")
    table.add_row("PgUp / PgDn", "翻阅内容区；输入焦点保持在命令框")
    table.add_row("Ctrl+End", "回到内容区底部并继续跟随新输出")
    table.add_row("Alt+PgUp / PgDn", "滚动内容超出高度上限的交互区")
    table.add_row("/help", "显示这份帮助")
    return table


def _running_status(kind: ResultKind) -> str:
    return {
        ResultKind.OBSERVE: "正在读取系统状态…",
        ResultKind.MARKET: "正在搜索市场标的…",
    }.get(kind, "正在执行…")
