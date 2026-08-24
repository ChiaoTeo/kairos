"""Single-screen guided command line for the Kairos Workbench."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
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

from kairospy.surface.console.models import (
    ObserveSnapshot,
    component_rows,
    recommended_action,
)

from ..widgets import (
    ActionItem,
    GuidedActionList,
    WorkbenchCommandInput,
    WorkbenchLog as RichLog,
    WorkspaceHeader,
)
from .navigation import (
    action_id,
    context_items,
    context_label,
    go_back,
    record_description,
    record_label,
)
from .results import ResultKey, ResultKind, parse_result_kind

if TYPE_CHECKING:
    from ..app import KairosWorkbenchApp
from .guided.catalog import HOME_ACTIONS, SECTION_ACTIONS, SECTION_LABELS
from .guided.account import (
    ACCOUNT_ACTIONS as RESOURCE_ACCOUNT_ACTIONS,
    execute as execute_account_action,
)
from .guided.business import (
    BusinessPromptState,
    actions as business_actions,
    execute as execute_business,
)
from .guided.market import (
    MarketFilePromptState,
    execute_file_action as execute_market_file_action,
    load_datasets as load_market_datasets,
    load_observation as load_market_observation,
    load_routes as load_market_routes,
    observation_renderable as market_observation_renderable,
    provider_actions as market_provider_actions,
    preview_file_action as preview_market_file_action,
    route_diagnostic_renderable,
    run_diagnostic as run_market_diagnostic,
    selected_market_actions,
)
from .guided.launch_market import (
    MARKET_COMPONENT_ACTIONS as STRATEGY_MARKET_ACTIONS,
    LaunchMarketPromptState,
    execute as execute_launch_market,
    preview as preview_launch_market,
)
from .guided.models import GuidedSession, PromptMode
from .guided.operations import (
    BUSINESS_ACTIONS as OPERATIONS_BUSINESS_ACTIONS,
    CONFIG_ACTIONS as OPERATIONS_CONFIG_ACTIONS,
    PROJECT_ACTIONS as OPERATIONS_PROJECT_ACTIONS,
    PROFILE_ACTIONS as OPERATIONS_PROFILE_ACTIONS,
    SERVICE_ACTIONS as OPERATIONS_SERVICE_ACTIONS,
    ProjectPromptState,
    execute_config as execute_operations_config,
    execute_operation,
    execute_project as execute_operations_project,
    execute_project_write as execute_operations_project_write,
    execute_service as execute_operations_service,
    list_services as list_operations_services,
    mutate_profile as mutate_operations_profile,
)
from .guided.orders import (
    ORDER_ACTIONS as ACCOUNT_ORDER_ACTIONS,
    OrderPromptState,
    execute as execute_order,
    preview as preview_order,
)
from .guided.kairos_command import (
    is_dangerous as is_dangerous_kairos_command,
    preview as preview_kairos_command,
    run as run_kairos_command,
)
from .guided.execution import (
    EXECUTION_ACTIONS as STRATEGY_EXECUTION_ACTIONS,
    ExecutionPromptState,
    execute as execute_connected_execution,
    preview as preview_connected_execution,
)
from .guided.reference import (
    INSTRUMENT_TYPE_ACTIONS,
    detail_actions as reference_detail_actions,
    detail_renderable as reference_detail_renderable,
    load_instrument_markets as load_reference_instrument_markets,
    load_records as load_reference_records,
    load_related as load_related_reference,
    records_renderable as reference_records_renderable,
)
from .guided.resources import (
    ResourceWizardState,
    detail_actions as resource_detail_actions,
    detail_renderable as resource_detail_renderable,
    execute_action as execute_resource_action,
    identity as resource_identity,
    list_records as list_resource_records,
    preview_action as preview_resource_action,
    records_renderable as resource_records_renderable,
    save_resource_wizard,
    summary as summarize_resources,
    summary_renderable as resource_summary_renderable,
)
from .guided.research import (
    DATA_ACTIONS as RESEARCH_DATA_ACTIONS,
    RESEARCH_ACTIONS as RESEARCH_WORKFLOW_ACTIONS,
    execute as execute_research,
    preview as preview_research,
)
from .guided.strategy import (
    ATTACH_ACTIONS as STRATEGY_ATTACH_ACTIONS,
    INSTANCE_ACTIONS as STRATEGY_INSTANCE_ACTIONS,
    LAUNCH_ACTIONS as STRATEGY_LAUNCH_ACTIONS,
    TIMELINE_ACTIONS as STRATEGY_TIMELINE_ACTIONS,
    LaunchWizardState,
    attach_snapshot as load_launch_attach_snapshot,
    components_renderable as launch_components_renderable,
    execute as execute_launch,
    export_timeline as export_launch_timeline,
    instance_overview as load_launch_instance_overview,
    instances_renderable as launch_instances_renderable,
    load_components as load_launch_components,
    load_instances as load_launch_instances,
    load_launches,
    load_timeline as load_launch_timeline,
    open_edit_launch_wizard,
    open_new_launch_wizard,
    preview as preview_launch,
    records_renderable as launch_records_renderable,
    save_launch_wizard,
    send_python as send_launch_python,
)
from .guided.workspace_market import (
    WORKSPACE_MARKET_ACTIONS,
    WorkspaceMarketPromptState,
    execute as execute_workspace_market,
    preview as preview_workspace_market,
)


class CommandLineScreen(Screen[None]):
    """One output region and one stateful, guided command input."""

    TITLE = "Kairos"
    SUB_TITLE = "命令"
    BINDINGS = [
        Binding("ctrl+l", "clear", "清屏", show=False),
        Binding("escape", "back", "取消或返回", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._interrupt_exit_pending = False
        self._active_worker: Worker[Any] | None = None
        self._attach_refresh_worker: Worker[Any] | None = None
        self.session = GuidedSession()

    @property
    def workbench_app(self) -> KairosWorkbenchApp:
        """Narrow Textual's generic App property to this Screen's owner."""

        return cast("KairosWorkbenchApp", self.app)

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield RichLog(
            id="command-output",
            wrap=True,
            highlight=False,
            markup=False,
        )
        yield GuidedActionList(
            *HOME_ACTIONS,
            id="guided-actions",
            classes="action-cards guided-actions",
            spacious=False,
        )
        yield Static("就绪", id="command-status")
        with Horizontal(id="command-bar"):
            yield Static("首页  /", id="command-context")
            yield WorkbenchCommandInput(id="command-input")
        yield Static(
            "数字选择  ·  /back 返回  ·  /help 帮助  ·  /exit 退出",
            id="command-hints",
        )

    def on_mount(self) -> None:
        self._write_welcome()
        self._show_context()
        self.app.set_focus(self._input())
        self.set_interval(1.0, self._refresh_launch_attach)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        value = event.value.strip()
        if not value and self.session.argument_prompt is None:
            return
        command_input = self._input()
        if self.session.prompt_mode is not PromptMode.SECRET:
            command_input.remember(value)
        command_input.value = ""
        self.submit(value)

    def submit(self, value: str) -> None:
        """Execute input through the same path used by the visible prompt."""

        value = value.strip()
        if not value and self.session.argument_prompt is None:
            return
        if self._active_worker is not None:
            self._write(
                Text("当前任务仍在运行；按 Ctrl+C 取消后再输入。", style="yellow")
            )
            self.app.set_focus(self._input())
            return
        is_secret = self.session.prompt_mode is PromptMode.SECRET
        self.workbench_app.transcript.record(
            "input",
            screen=type(self).__name__,
            value="<redacted>" if is_secret else value,
            secret=is_secret,
        )
        self._write_prompt("••••••••" if is_secret and value else value)
        if self.session.confirmation_prompt is not None:
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
                self._write(
                    Text(
                        "当前正在等待确认；请输入 /confirm、/cancel 或 /exit。",
                        style="yellow",
                    )
                )
                self.app.set_focus(self._input())
                return
            self._interrupt_exit_pending = False
            self._dispatch(command, arguments)
            self.app.set_focus(self._input())
            return
        argument_prompt = self.session.argument_prompt
        if argument_prompt is not None:
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
                and pending_command in {"back", "b", "cancel"}
                and not pending_arguments
            ):
                self.action_back()
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
                    self._cancel_resource_wizard()
                self.session.home()
                self._show_context()
                return
            if (
                is_workbench_command
                and pending_command in {"help", "?"}
                and not pending_arguments
            ):
                self._write(_help_renderable(self.session.context))
                self._write_next_step("当前仍在等待参数；输入 /back 取消本步。")
                return
            pending = argument_prompt.action
            self.session.finish_prompt()
            self._input().password = False
            self._input().placeholder = "输入命令；Enter 提交"
            self._dispatch(pending, (value,))
            self.app.set_focus(self._input())
            return
        if not value.startswith("/") and not value.isdecimal():
            self._dispatch_kairos(value)
            return
        command, arguments = _parse_command(value)
        self._dispatch(command, arguments)
        self.app.set_focus(self._input())

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
            self._record_action(
                "system.observe",
                argv[1:],
                equivalent_command=self._observe_command(),
            )
            self._run("observe", self._read_observe)
            return
        self._record_action(
            "kairos.command",
            argv,
            equivalent_command=equivalent,
        )

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_kairos_command(argv)
            return run_kairos_command(self.workbench_app.state, argv)

        if is_dangerous_kairos_command(argv) and not self.workbench_app.state.yes:
            self.request_confirmation(
                shlex.join(equivalent), operation, result_kind="kairos-command"
            )
            return
        self._run(
            "kairos-command",
            operation,
            status=f"正在执行 {shlex.join(equivalent)}…",
        )

    def _dispatch(self, command: str, arguments: tuple[str, ...]) -> None:
        if command in {"home", "/"}:
            self.session.home()
            self._show_context()
        elif command in {"exit", "quit", "q"}:
            self.workbench_app.action_quit()
        elif command in {"back", "b"}:
            self.action_back()
        elif command in {"help", "?"}:
            self._write(_help_renderable(self.session.context))
        elif command == "clear":
            self.action_clear()
        elif command == "copy":
            self.workbench_app.action_copy_page()
        elif command == "transcript":
            self._write(
                Text(
                    str(
                        self.workbench_app.transcript.path
                        or "Transcript 仅当前进程可用"
                    ),
                    style="dim",
                )
            )
        elif command.startswith("reference:"):
            kind = command.partition(":")[2]
            query = " ".join(arguments).strip()
            self._run(
                f"reference:{kind}",
                lambda: self._find_reference_records(kind, query),
            )
        elif self._dispatch_operations_command(command, arguments):
            pass
        elif self._dispatch_research_command(command, arguments):
            pass
        elif self._dispatch_resource_command(command, arguments):
            pass
        elif self._dispatch_strategy_command(command, arguments):
            pass
        elif self._dispatch_flow_field_command(command, arguments):
            pass
        elif command == "observe":
            self._record_action(
                "system.observe",
                arguments,
                equivalent_command=self._observe_command(),
            )
            self._run("observe", self._read_observe)
        elif command == "market":
            query = " ".join(arguments).strip()
            if query:
                self._record_action("market.find", arguments)
                self._run("market", lambda: self._find_markets(query))
            else:
                self._request_argument(
                    "market",
                    "请输入市场代码或名称",
                    "例如 AAPL、BTCUSDT；输入 /back 取消。",
                )
        elif command in {"confirm", "yes", "y"}:
            self._confirm_pending()
        elif command in {"cancel", "no", "n"}:
            self.action_cancel_pending()
        else:
            if self._dispatch_context(command, arguments):
                return
            self._write_error(f"未知操作：{command or value_or_unknown(arguments)}")
            self._show_context()

    def _dispatch_strategy_command(
        self, command: str, arguments: tuple[str, ...]
    ) -> bool:
        is_strategy_new = command == "new" and self.session.context[:1] == ("strategy",)
        if not (is_strategy_new or command.startswith("strategy:")):
            return False

        if command == "new" and self.session.context[:1] == ("strategy",):
            self._request_argument(
                "strategy:launch-id",
                "请输入新 Launch ID",
                "例如 paper-demo；输入 /back 取消。",
            )
        elif command == "strategy:launch-id":
            launch_id = " ".join(arguments).strip()
            try:
                wizard = open_new_launch_wizard(self.workbench_app.state, launch_id)
            except (OSError, ValueError) as error:
                self._write_error(str(error))
                self._request_argument(
                    "strategy:launch-id",
                    "请输入新 Launch ID",
                    "例如 paper-demo；输入 /back 取消。",
                )
            else:
                self._start_launch_wizard(wizard)
        elif command.startswith("strategy:launch-field:"):
            field_name = command.removeprefix("strategy:launch-field:")
            wizard = self.session.launch_wizard
            if not isinstance(wizard, LaunchWizardState):
                self._write_error("Launch 配置向导已经失效，请重新开始。")
                self.session.enter("strategy")
                self._show_context()
            else:
                try:
                    wizard.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                    prompt = wizard.next_prompt()
                    if prompt is not None:
                        name, label, detail = prompt
                        self._request_argument(
                            f"strategy:launch-field:{name}", label, detail
                        )
                else:
                    self._advance_launch_wizard()
        elif command == "strategy:launch-save-mode":
            mode = " ".join(arguments).strip().lower() or "draft"
            if mode not in {"draft", "publish"}:
                self._write_error("请输入 draft 或 publish")
                self._request_argument(
                    "strategy:launch-save-mode",
                    "保存方式（draft / publish）",
                    "draft 仅保存草稿；publish 校验并发布。",
                )
            else:
                self._request_launch_wizard_confirmation(publish=mode == "publish")
        elif command == "strategy:timeline-export":
            destination = " ".join(arguments).strip()
            record = self.session.selected_launch_record
            if record is None:
                self._write_error("实例上下文已经失效，请重新选择。")
                self.session.enter("strategy")
                self._show_context()
            else:
                launch_id = str(record["launch_id"])
                instance_id = str(record["instance_id"])
                mode = str(record["mode"])

                def export_operation() -> Any:
                    if (
                        self.workbench_app.state.dry_run
                        or self.workbench_app.state.no_exec
                    ):
                        return {
                            "status": "preview",
                            "action": "timeline-export",
                            "destination": destination,
                        }
                    return export_launch_timeline(
                        self.workbench_app.state,
                        launch_id,
                        instance_id,
                        mode,
                        destination,
                    )

                if (
                    self.workbench_app.state.yes
                    or self.workbench_app.state.dry_run
                    or self.workbench_app.state.no_exec
                ):
                    self._run("strategy-timeline-export", export_operation)
                else:
                    self.request_confirmation(
                        f"导出时间线到 {destination}",
                        export_operation,
                        result_kind="strategy-timeline-export",
                    )
        elif command == "strategy:python":
            record = self.session.selected_launch_record
            source = " ".join(arguments)
            if record is None:
                self._write_error("Launch 上下文已经失效，请重新选择。")
                self.session.enter("strategy")
                self._show_context()
            else:
                launch_id = str(record["launch_id"])

                def python_operation() -> Any:
                    if (
                        self.workbench_app.state.dry_run
                        or self.workbench_app.state.no_exec
                    ):
                        return {
                            "status": "preview",
                            "action": "interactive.python",
                            "launch_id": launch_id,
                            "source": source,
                        }
                    return send_launch_python(
                        self.workbench_app.state, launch_id, source
                    )

                if (
                    self.workbench_app.state.yes
                    or self.workbench_app.state.dry_run
                    or self.workbench_app.state.no_exec
                ):
                    self._run("strategy-attach", python_operation)
                else:
                    self.request_confirmation(
                        f"向 Launch {launch_id} 发送 Strategy Python",
                        python_operation,
                        result_kind="strategy-attach",
                    )
        return True

    def _dispatch_flow_field_command(
        self, command: str, arguments: tuple[str, ...]
    ) -> bool:
        prefixes = (
            "business:field:",
            "execution:field:",
            "launch-market:field:",
            "market-file:field:",
            "workspace-market:field:",
        )
        if not command.startswith(prefixes):
            return False

        if command.startswith("business:field:"):
            field_name = command.removeprefix("business:field:")
            prompt = self.session.business_prompt
            if not isinstance(prompt, BusinessPromptState):
                self._write_error("业务工具参数向导已经失效，请重新选择操作。")
                self.session.enter("operations", "business")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_business_prompt()
        elif command.startswith("execution:field:"):
            field_name = command.removeprefix("execution:field:")
            prompt = self.session.execution_prompt
            if not isinstance(prompt, ExecutionPromptState):
                self._write_error("Execution 参数向导已经失效，请重新选择操作。")
                self.session.context = ("strategy", "execution")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_execution_prompt()
        elif command.startswith("launch-market:field:"):
            field_name = command.removeprefix("launch-market:field:")
            prompt = self.session.launch_market_prompt
            if not isinstance(prompt, LaunchMarketPromptState):
                self._write_error("Market 参数向导已经失效，请重新选择操作。")
                self.session.context = ("strategy", "market")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_launch_market_prompt()
        elif command.startswith("market-file:field:"):
            field_name = command.removeprefix("market-file:field:")
            prompt = self.session.market_file_prompt
            if not isinstance(prompt, MarketFilePromptState):
                self._write_error("Market 文件操作参数向导已经失效，请重新选择操作。")
                self.session.enter("market")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_market_file_prompt()
        elif command.startswith("workspace-market:field:"):
            field_name = command.removeprefix("workspace-market:field:")
            prompt = self.session.workspace_market_prompt
            if not isinstance(prompt, WorkspaceMarketPromptState):
                self._write_error("Workspace Market 参数向导已经失效。")
                self.session.context = ("market", "connected")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_workspace_market_prompt()
        return True

    def _dispatch_resource_command(
        self, command: str, arguments: tuple[str, ...]
    ) -> bool:
        is_resource_new = command == "new" and self.session.context[:1] == (
            "resources",
        )
        if not (
            command.startswith("resource:")
            or is_resource_new
            or command == "account:fees"
            or command.startswith("order:field:")
        ):
            return False

        if command.startswith("resource:"):
            action = command.partition(":")[2]
            value = " ".join(arguments).strip()
            if action.startswith("setup-field:"):
                field_name = action.removeprefix("setup-field:")
                wizard = self.session.resource_wizard
                if not isinstance(wizard, ResourceWizardState):
                    self._write_error("资源配置向导已经失效，请重新开始。")
                    self.session.enter("resources")
                    self._show_context()
                else:
                    try:
                        wizard.accept(field_name, value)
                    except ValueError as error:
                        self._write_error(str(error))
                        self._advance_resource_wizard()
                    else:
                        self._advance_resource_wizard()
            elif action == "model-test":
                self._request_resource_confirmation("test", value=value)
            elif action == "notification-mode":
                self._run_resource_action("validate", value=value or "paper")
            elif action in {"notification-attach", "notification-detach"}:
                self.session.resource_action = (
                    "attach" if action == "notification-attach" else "detach"
                )
                self.session.resource_launch_id = value
                if self.session.resource_action == "attach":
                    self._request_argument(
                        "resource:notification-route",
                        "请输入通知 route；直接回车使用 signals",
                        "输入 /back 取消。",
                    )
                else:
                    self._request_resource_confirmation("detach", launch_id=value)
            elif action == "notification-route":
                self._request_resource_confirmation(
                    "attach",
                    value=value or "signals",
                    launch_id=self.session.resource_launch_id,
                )
        elif command == "new" and self.session.context[:1] == ("resources",):
            kind = self.session.resource_kind
            if kind is None:
                self._write_error("请先进入交易账户、市场数据、AI 模型或通知提醒列表。")
                self._show_context()
            else:
                self._start_resource_wizard(ResourceWizardState(kind))
        elif command == "account:fees":
            record = self.session.selected_resource
            if record is None:
                self._write_error("账户上下文已经失效，请重新选择。")
                self.session.enter("resources")
                self._show_context()
            else:
                value = " ".join(arguments).strip()
                self._run(
                    "account-result",
                    lambda: execute_account_action(
                        self.workbench_app.state,
                        record,
                        "fees",
                        value,
                    ),
                )
        elif command.startswith("order:field:"):
            field_name = command.removeprefix("order:field:")
            prompt = self.session.order_prompt
            if not isinstance(prompt, OrderPromptState):
                self._write_error("订单参数向导已经失效，请重新选择操作。")
                self.session.context = ("resources", "account-orders")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_order_prompt()
        return True

    def _dispatch_operations_command(
        self, command: str, arguments: tuple[str, ...]
    ) -> bool:
        if not (
            command == "operations-config:explain"
            or command.startswith("operations-project:field:")
            or command == "operations-profile:name"
        ):
            return False

        if command == "operations-config:explain":
            name = " ".join(arguments).strip()
            self._run(
                "operations-result",
                lambda: execute_operations_config(
                    self.workbench_app.state, "explain", name
                ),
            )
        elif command.startswith("operations-project:field:"):
            field_name = command.removeprefix("operations-project:field:")
            prompt = self.session.project_prompt
            if not isinstance(prompt, ProjectPromptState):
                self._write_error("项目参数向导已经失效，请重新选择操作。")
                self.session.enter("operations", "project")
                self._show_context()
            else:
                try:
                    prompt.accept(field_name, " ".join(arguments))
                except ValueError as error:
                    self._write_error(str(error))
                self._advance_project_prompt()
        elif command == "operations-profile:name":
            action = self.session.profile_action
            name = " ".join(arguments).strip()
            if action not in {"create", "use"} or not name:
                self._write_error("Profile 操作或名称无效，请重新选择。")
                self.session.profile_action = None
                self.session.context = ("operations", "profiles")
                self._show_context()
            else:

                def profile_operation() -> Any:
                    if (
                        self.workbench_app.state.dry_run
                        or self.workbench_app.state.no_exec
                    ):
                        return {"status": "preview", "action": action, "profile": name}
                    return mutate_operations_profile(
                        self.workbench_app.state, action, name
                    )

                if (
                    self.workbench_app.state.yes
                    or self.workbench_app.state.dry_run
                    or self.workbench_app.state.no_exec
                ):
                    self._run("operations-profile-result", profile_operation)
                else:
                    self.request_confirmation(
                        f"{action} Profile {name}",
                        profile_operation,
                        result_kind="operations-profile-result",
                    )
        return True

    def _dispatch_research_command(
        self, command: str, arguments: tuple[str, ...]
    ) -> bool:
        if not command.startswith("research:"):
            return False
        action = command.partition(":")[2]
        value = " ".join(arguments)
        if action == "execute-data":
            self.session.research_action = action
            self.session.research_primary = value
            self._request_argument(
                "research:execute-data-hash",
                "请输入已审阅的 plan hash；直接回车可跳过",
                "输入 /back 取消。",
            )
        elif action == "publish-gate":
            self.session.research_action = action
            self.session.research_primary = value
            self._request_argument(
                "research:publish-gate-evidence",
                "请输入 research-evidence.json 路径",
                "输入 /back 取消。",
            )
        elif action == "execute-data-hash":
            self._request_research_confirmation(
                "execute-data", self.session.research_primary, value or None
            )
        elif action == "publish-gate-evidence":
            self._request_research_confirmation(
                "publish-gate", self.session.research_primary, value
            )
        elif action == "lock-plan":
            self._request_research_confirmation(action, value, None)
        else:
            self._run(
                "research-result",
                lambda: execute_research(self.workbench_app.state, action, value),
            )
        return True

    def _dispatch_strategy_context(self, command: str) -> bool:
        """Handle Strategy section and nested contexts."""

        section = "strategy"
        if section == "strategy" and self.session.context[1:] == ("attach",):
            record = self.session.selected_launch_record
            if record is None:
                self.session.enter("strategy")
                self._show_context()
                return True
            action = action_id(STRATEGY_ATTACH_ACTIONS, command)
            if action is None:
                return False
            if action == "refresh":
                self._refresh_launch_attach(force=True)
            elif action == "pause":
                self.session.launch_attach_paused = (
                    not self.session.launch_attach_paused
                )
                self._write(
                    Text(
                        "已暂停后台刷新。"
                        if self.session.launch_attach_paused
                        else "已继续后台刷新。",
                        style="dim",
                    )
                )
                if not self.session.launch_attach_paused:
                    self._refresh_launch_attach(force=True)
                self._show_context()
            else:
                self._request_argument(
                    "strategy:python",
                    "请输入一行发送到当前 Strategy 的 Python",
                    "执行前会再次确认；输入 /back 取消。",
                )
            return True

        if section == "strategy" and self.session.context[1:] == ("instance",):
            record = self.session.selected_launch_record
            if record is None:
                self.session.enter("strategy")
                self._show_context()
                return True
            action = action_id(STRATEGY_INSTANCE_ACTIONS, command)
            if action is None:
                return False
            launch_id = str(record["launch_id"])
            instance_id = str(record["instance_id"])
            mode = str(record["mode"])
            if action == "overview":
                self._run(
                    "strategy-instance-result",
                    lambda: load_launch_instance_overview(
                        self.workbench_app.state,
                        launch_id,
                        instance_id,
                    ),
                )
            elif action == "components":
                self._run(
                    "strategy-components",
                    lambda: load_launch_components(
                        self.workbench_app.state,
                        launch_id,
                        instance_id,
                        mode,
                    ),
                )
            else:
                self._run(
                    "strategy-timeline",
                    lambda: load_launch_timeline(
                        self.workbench_app.state,
                        launch_id,
                        instance_id,
                        mode,
                    ),
                )
            return True

        if section == "strategy" and self.session.context[1:] == ("timeline",):
            record = self.session.selected_launch_record
            if record is None:
                self.session.enter("strategy")
                self._show_context()
                return True
            action = action_id(STRATEGY_TIMELINE_ACTIONS, command)
            if action is None:
                return False
            if action == "refresh":
                self._run(
                    "strategy-timeline",
                    lambda: load_launch_timeline(
                        self.workbench_app.state,
                        str(record["launch_id"]),
                        str(record["instance_id"]),
                        str(record["mode"]),
                    ),
                )
            else:
                self._request_argument(
                    "strategy:timeline-export",
                    "请输入导出文件路径",
                    f"例如 {record['launch_id']}-{record['instance_id']}-timeline.jsonl；"
                    "输入 /back 取消。",
                )
            return True

        if section == "strategy" and self.session.context[1:] == ("execution",):
            record = self.session.selected_launch_record
            if record is None:
                self.session.enter("strategy")
                self._show_context()
                return True
            action = action_id(STRATEGY_EXECUTION_ACTIONS, command)
            if action is None:
                return False
            self.session.execution_prompt = ExecutionPromptState(action, record)
            self._advance_execution_prompt()
            return True

        if section == "strategy" and self.session.context[1:] == ("market",):
            record = self.session.selected_launch_record
            if record is None:
                self.session.enter("strategy")
                self._show_context()
                return True
            action = action_id(STRATEGY_MARKET_ACTIONS, command)
            if action is None:
                return False
            selected_market = self.workbench_app.state.selected_market
            default_market = (
                str(selected_market.id)
                if selected_market is not None and hasattr(selected_market, "id")
                else ""
            )
            self.session.launch_market_prompt = LaunchMarketPromptState(
                action, record, default_market
            )
            self._advance_launch_market_prompt()
            return True

        if (
            section == "strategy"
            and self.session.context[1:] == ("components",)
            and self.session.visible_records
        ):
            record = _record_choice(self.session.visible_records, command)
            if record is None:
                return False
            if str(record.get("component") or "") == "market":
                self.session.context = ("strategy", "market")
                self._write(Panel(Pretty(record, expand_all=True), title="Market"))
                self._show_context()
                return True
            if str(record.get("component") or "") == "execution":
                self.session.context = ("strategy", "execution")
                self._write(Panel(Pretty(record, expand_all=True), title="Execution"))
                self._show_context()
                return True
            self._write(Panel(Pretty(record, expand_all=True), title="实例组件"))
            self._show_context()
            return True

        if (
            section == "strategy"
            and self.session.context[1:] == ("instances",)
            and self.session.visible_records
        ):
            instance = _record_choice(self.session.visible_records, command)
            if instance is None:
                return False
            selected = dict(self.session.selected_launch_record or {})
            selected.update(dict(instance))
            self.session.selected_launch_record = selected
            self.session.context = ("strategy", "instance")
            self.workbench_app.state.selected_launch_instance = str(
                instance["instance_id"]
            )
            self.workbench_app.state.selected_launch_mode = str(instance["mode"])
            self._write(Panel(Pretty(instance, expand_all=True), title="运行实例"))
            self._show_context()
            return True

        if section == "strategy" and self.session.context[1:] == ("selected",):
            record = self.session.selected_launch_record
            if record is None:
                self.session.enter("strategy")
                self._show_context()
                return True
            action = action_id(STRATEGY_LAUNCH_ACTIONS, command)
            if action is None:
                return False
            if action == "edit":
                try:
                    wizard = open_edit_launch_wizard(self.workbench_app.state, record)
                except (OSError, ValueError) as error:
                    self._write_error(str(error))
                    self._show_context()
                else:
                    self._start_launch_wizard(wizard)
                return True
            if action == "attach":
                self.session.context = ("strategy", "attach")
                self.session.launch_attach_paused = False
                self.session.launch_attach_seen = ()
                self._show_context()
                self._refresh_launch_attach(force=True)
                return True
            if action == "instances":
                launch_id = str(record["launch_id"])
                self._run(
                    "strategy-instances",
                    lambda: load_launch_instances(self.workbench_app.state, launch_id),
                )
                return True

            def launch_operation() -> Any:
                if action in {"start", "stop", "restart"} and (
                    self.workbench_app.state.dry_run or self.workbench_app.state.no_exec
                ):
                    return preview_launch(record, action)
                return execute_launch(self.workbench_app.state, record, action)

            if (
                action in {"start", "stop", "restart"}
                and not self.workbench_app.state.yes
                and not (
                    self.workbench_app.state.dry_run or self.workbench_app.state.no_exec
                )
            ):
                self.request_confirmation(
                    f"kairos launch {action} {record['launch_id']}",
                    launch_operation,
                    result_kind="strategy-result",
                )
            else:
                self._run("strategy-result", launch_operation)
            return True

        if (
            section == "strategy"
            and self.session.context[1:] == ("launches",)
            and self.session.visible_records
        ):
            record = _record_choice(self.session.visible_records, command)
            if record is None:
                return False
            selected = dict(record)
            self.session.selected_launch_record = selected
            self.session.context = ("strategy", "selected")
            self.workbench_app.state.selected_launch = str(selected["launch_id"])
            self._write(Panel(Pretty(selected, expand_all=True), title="Launch"))
            self._show_context()
            return True
        action = action_id(SECTION_ACTIONS[section], command)
        if action is None:
            return False
        if action in {"once", "observe", "doctor"}:
            self._run("observe", self._read_observe)
        elif action == "launch":
            self._run(
                "strategy-launches",
                lambda: load_launches(self.workbench_app.state),
            )
        return True

    def _dispatch_resource_context(self, command: str) -> bool:
        """Handle Resource section and nested Account contexts."""

        section = "resources"
        if section == "resources" and self.session.context[1:] == ("selected",):
            kind = self.session.resource_kind
            record = self.session.selected_resource
            if kind is None or record is None:
                self.session.enter("resources")
                self._show_context()
                return True
            action = action_id(resource_detail_actions(kind), command)
            if action is None:
                return False
            if action == "advanced":
                self._run_resource_action(action)
            elif action == "models" and kind == "models":
                self._write(
                    Panel(
                        Pretty({"models": list(record.get("models") or ())}),
                        title="已保存模型",
                    )
                )
                self._show_context()
            elif action == "operations" and kind == "accounts":
                account = resource_identity(kind, record)
                self._write(
                    Panel(
                        f"已选择账户 {account}。可直接输入 "
                        f"account overview {account} 等 kairos 业务命令。",
                        title="账户运行查询",
                        border_style="cyan",
                    )
                )
                self.session.context = ("resources", "account-operations")
                self._show_context()
            elif action == "edit":
                self._start_resource_wizard(ResourceWizardState(kind, dict(record)))
            elif action == "test" and kind == "models":
                self._request_argument(
                    "resource:model-test",
                    "请输入用于连接测试的模型 ID",
                    "输入 /back 取消。",
                )
            elif action == "validate" and kind == "notifications":
                self._request_argument(
                    "resource:notification-mode",
                    "请输入运行模式；直接回车使用 paper",
                    "输入 /back 取消。",
                )
            elif action in {"attach", "detach"} and kind == "notifications":
                self._request_argument(
                    f"resource:notification-{action}",
                    "请输入 Launch ID",
                    "输入 /back 取消。",
                )
            elif action in {"test", "toggle", "delete"}:
                self._request_resource_confirmation(action)
            else:
                self._write_error(f"{action} 正在迁移为单输入参数/确认向导。")
                self._show_context()
            return True

        if section == "resources" and self.session.context[1:] == (
            "account-operations",
        ):
            record = self.session.selected_resource
            if record is None:
                self.session.enter("resources")
                self._show_context()
                return True
            action = action_id(RESOURCE_ACCOUNT_ACTIONS, command)
            if action is None:
                return False
            if action == "fees":
                self._request_argument(
                    "account:fees",
                    "费率范围（产品:交易对）",
                    "直接回车使用 spot:BTCUSDT；输入 /back 取消。",
                )
            elif action == "orders":
                self.session.context = ("resources", "account-orders")
                self._show_context()
            else:
                self._run(
                    "account-result",
                    lambda: execute_account_action(
                        self.workbench_app.state,
                        record,
                        action,
                    ),
                )
            return True

        if section == "resources" and self.session.context[1:] == ("account-orders",):
            record = self.session.selected_resource
            if record is None:
                self.session.enter("resources")
                self._show_context()
                return True
            action = action_id(ACCOUNT_ORDER_ACTIONS, command)
            if action is None:
                return False
            self.session.order_prompt = OrderPromptState(action, dict(record))
            self._advance_order_prompt()
            return True

        if (
            section == "resources"
            and len(self.session.context) > 1
            and self.session.visible_records
        ):
            record = _record_choice(self.session.visible_records, command)
            if record is None:
                return False
            kind = self.session.resource_kind
            if kind is None:
                return False
            selected = dict(record)
            self.session.selected_resource = selected
            self.session.context = ("resources", "selected")
            if kind == "accounts":
                self.workbench_app.state.selected_account = resource_identity(
                    kind, selected
                )
            self._write(resource_detail_renderable(kind, selected))
            self._show_context()
            return True
        action = action_id(SECTION_ACTIONS[section], command)
        if action is None:
            return False
        if action == "check":
            self._run(
                "resources-summary",
                lambda: summarize_resources(self.workbench_app.state),
            )
        else:
            self.session.resource_kind = action
            self._run(
                f"resources-list:{action}",
                lambda: list_resource_records(self.workbench_app.state, action),
            )
        return True

    def _dispatch_operations_context(self, command: str) -> bool:
        """Handle System Operations section and nested contexts."""

        section = "operations"
        if section == "operations" and self.session.context[1:] == ("service",):
            component = self.session.selected_service
            if component is None:
                self.session.enter("operations")
                self._show_context()
                return True
            action = action_id(OPERATIONS_SERVICE_ACTIONS, command)
            if action is None:
                return False

            def service_operation() -> Any:
                if action in {"start", "stop", "restart"} and (
                    self.workbench_app.state.dry_run or self.workbench_app.state.no_exec
                ):
                    return {
                        "status": "preview",
                        "component": component,
                        "action": action,
                    }
                return execute_operations_service(
                    self.workbench_app.state, component, action
                )

            if (
                action in {"start", "stop", "restart"}
                and not self.workbench_app.state.yes
                and not (
                    self.workbench_app.state.dry_run or self.workbench_app.state.no_exec
                )
            ):
                self.request_confirmation(
                    f"kairos system {action} --component {component}",
                    service_operation,
                )
            else:
                self._run("operations-result", service_operation)
            return True

        if section == "operations" and self.session.context[1:] == ("project",):
            action = action_id(OPERATIONS_PROJECT_ACTIONS, command)
            if action is None:
                return False
            if action in {"status", "doctor"}:
                self._run(
                    "operations-result",
                    lambda: execute_operations_project(
                        self.workbench_app.state, action
                    ),
                )
            else:
                self.session.project_prompt = ProjectPromptState(action)
                self._advance_project_prompt()
            return True

        if section == "operations" and self.session.context[1:] == ("config",):
            action = action_id(OPERATIONS_CONFIG_ACTIONS, command)
            if action is None:
                return False
            if action == "explain":
                self._request_argument(
                    "operations-config:explain",
                    "请输入配置名称",
                    "例如 launches/demo-backtest；输入 /back 取消。",
                )
            elif action == "models":
                self.session.resource_kind = "models"
                self._run(
                    "resources-list:models",
                    lambda: list_resource_records(self.workbench_app.state, "models"),
                )
            elif action == "profiles":
                self.session.context = ("operations", "profiles")
                self._show_context()
            else:
                self._run(
                    "operations-result",
                    lambda: execute_operations_config(self.workbench_app.state, action),
                )
            return True

        if section == "operations" and self.session.context[1:] == ("profiles",):
            action = action_id(OPERATIONS_PROFILE_ACTIONS, command)
            if action is None:
                return False
            if action == "list":
                self._run(
                    "operations-profile-result",
                    lambda: execute_operations_config(
                        self.workbench_app.state, "profiles"
                    ),
                )
            else:
                self.session.profile_action = action
                self._request_argument(
                    "operations-profile:name",
                    "请输入 Profile 名称",
                    "写入前会显示确认；输入 /back 取消。",
                )
            return True

        if section == "operations" and self.session.context[1:] == ("business",):
            action = action_id(OPERATIONS_BUSINESS_ACTIONS, command)
            if action is None:
                return False
            self.session.enter("operations", "business", action)
            self._show_context()
            return True

        if (
            section == "operations"
            and len(self.session.context) == 3
            and self.session.context[1] == "business"
        ):
            tool = self.session.context[2]
            action = action_id(business_actions(tool), command)
            if action is None:
                return False
            self.session.business_prompt = BusinessPromptState(tool, action)
            self._advance_business_prompt()
            return True

        if section == "operations" and self.session.context[1:] == ("services",):
            if self.session.visible_records:
                record = _record_choice(self.session.visible_records, command)
                if record is None:
                    return False
                component = str(record.get("component") or "")
                if not component:
                    self._write_error("所选服务没有组件标识。")
                    return True
                self.session.selected_service = component
                self.session.context = ("operations", "service")
                self._write(Panel(Pretty(record, expand_all=True), title=component))
                self._show_context()
                return True

        action = action_id(SECTION_ACTIONS[section], command)
        if action is None:
            return False
        if action == "observe":
            self._run("observe", self._read_observe)
        elif action in {"doctor", "migration", "workspace"}:
            self._run(
                "operations-result",
                lambda: execute_operation(self.workbench_app.state, action),
            )
        elif action == "repair":

            def operation() -> Any:
                if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                    return {"status": "preview", "action": "repair"}
                return execute_operation(self.workbench_app.state, action)

            if (
                self.workbench_app.state.yes
                or self.workbench_app.state.dry_run
                or self.workbench_app.state.no_exec
            ):
                self._run("operations-result", operation)
            else:
                self.request_confirmation("修复 stale 运行资源", operation)
        elif action == "services":
            self._run(
                "operations-services",
                lambda: list_operations_services(self.workbench_app.state),
            )
        elif action in {"project", "config", "business"}:
            self.session.enter("operations", action)
            self._show_context()
        return True

    def _dispatch_research_context(self, command: str) -> bool:
        if len(self.session.context) == 1:
            action = action_id(SECTION_ACTIONS["research"], command)
            if action is None:
                return False
            self.session.enter("research", action)
            self._show_context()
            return True

        items = (
            RESEARCH_DATA_ACTIONS
            if self.session.context[1] == "data"
            else RESEARCH_WORKFLOW_ACTIONS
        )
        action = action_id(items, command)
        if action is None:
            return False
        if action in {"datasets", "sets"}:
            self._run(
                "research-result",
                lambda: execute_research(self.workbench_app.state, action),
            )
            return True
        prompts = {
            "inspect": "请输入 Dataset ID",
            "plan-data": "请输入 requirements.json 路径",
            "execute-data": "请输入 requirements.json 路径",
            "execution": "请输入 plan hash",
            "set": "请输入 Dataset Set alias",
            "data-gate": "请输入 composition hash",
            "lock-plan": "请输入 research-plan.json 路径",
            "show-plan": "请输入 plan hash",
            "publish-gate": "请输入 research-plan.json 路径",
            "show-gate": "请输入 plan hash",
        }
        self._request_argument(
            f"research:{action}",
            prompts[action],
            "输入 /back 取消。",
        )
        return True

    def _dispatch_market_context(self, command: str) -> bool:
        if self.session.context[1:] == ("selected",):
            market = self.workbench_app.state.selected_market
            if market is None:
                self.session.enter("market")
                self._show_context()
                return True
            action = action_id(selected_market_actions(market), command)
            if action is None:
                return False
            if action in {"validate", "universe"}:
                self._run(
                    f"market-diagnostic:{action}",
                    lambda: run_market_diagnostic(
                        self.workbench_app.state, market, action
                    ),
                )
            else:
                self.session.market_observation = action
                self._run(
                    "market-routes",
                    lambda: load_market_routes(
                        self.workbench_app.state, market, action
                    ),
                )
            return True

        if self.session.context[1:] == ("connected",):
            action = action_id(WORKSPACE_MARKET_ACTIONS, command)
            if action is None:
                return False
            selected_market = self.workbench_app.state.selected_market
            default_market = (
                str(selected_market.id)
                if selected_market is not None and hasattr(selected_market, "id")
                else ""
            )
            self.session.workspace_market_prompt = WorkspaceMarketPromptState(
                action, default_market
            )
            self._advance_workspace_market_prompt()
            return True

        if len(self.session.context) > 1 and self.session.visible_records:
            record = _record_choice(self.session.visible_records, command)
            if record is None:
                return False
            if self.session.context[1] == "providers":
                provider = str(record.get("provider") or "")
                if not provider:
                    self._write_error("所选数据源没有 Provider 标识。")
                    return True
                self._request_market_observation(provider)
                return True
            self.workbench_app.state.selected_market = record
            self.session.context = ("market", "selected")
            self._write(_record_detail_renderable(record, section="market"))
            if self.session.market_purpose in {"download", "replay"}:
                self.session.market_file_prompt = MarketFilePromptState(
                    self.session.market_purpose, record
                )
                self._advance_market_file_prompt()
            else:
                self._show_context()
            return True

        action = action_id(SECTION_ACTIONS["market"], command)
        if action is None:
            return False
        if action in {"search", "download", "replay", "diagnostics", "advanced"}:
            self.session.market_purpose = action
            self._request_argument(
                "market",
                "请输入市场代码、名称或完整 Market ID",
                "例如 AAPL、BTCUSDT；输入 /back 或按 Esc 取消。",
            )
        elif action == "datasets":
            self._run(
                "market-datasets",
                lambda: load_market_datasets(self.workbench_app.state),
            )
        elif action == "connected":
            self.session.context = ("market", "connected")
            self._show_context()
        return True

    def _dispatch_reference_context(self, command: str) -> bool:
        if self.session.context[1:] == ("instrument-types",):
            instrument_type = action_id(INSTRUMENT_TYPE_ACTIONS, command)
            if instrument_type is None:
                return False
            self.session.reference_kind = "instruments"
            self.session.reference_instrument_type = instrument_type
            self._request_argument(
                "reference:instruments",
                "输入代码或名称；直接回车浏览",
                "输入 /back 或按 Esc 取消并返回合约类型菜单。",
            )
            return True

        if self.session.context[1:] == ("selected",):
            record = self.workbench_app.state.selected_reference
            kind = self.session.reference_kind
            if record is None or kind is None:
                self.session.enter("reference")
                self._show_context()
                return True
            action = action_id(reference_detail_actions(kind), command)
            if action is None:
                return False
            if action in {"summary", "technical"}:
                self._write(
                    reference_detail_renderable(
                        record, kind, technical=action == "technical"
                    )
                )
                self._show_context()
            elif action == "markets" and kind in {"instruments", "option-chain"}:
                self._run(
                    "reference-related",
                    lambda: load_reference_instrument_markets(
                        self.workbench_app.state,
                        record,
                    ),
                )
            else:
                self._run(
                    "reference-related",
                    lambda: load_related_reference(
                        self.workbench_app.state,
                        kind,
                        record,
                    ),
                )
            return True

        if len(self.session.context) > 1 and self.session.visible_records:
            record = _record_choice(self.session.visible_records, command)
            if record is None:
                return False
            self.workbench_app.state.selected_reference = record
            self.session.context = ("reference", "selected")
            self._write(
                reference_detail_renderable(record, self.session.reference_kind)
            )
            self._show_context()
            return True

        action = action_id(SECTION_ACTIONS["reference"], command)
        if action is None:
            return False
        if action == "instruments":
            self.session.reference_kind = action
            self.session.reference_instrument_type = None
            self.session.context = ("reference", "instrument-types")
            self._show_context()
            return True
        prompt = (
            "请输入标的合约 ID"
            if action == "option-chain"
            else "输入代码或名称；直接回车浏览"
        )
        self.session.reference_kind = action
        self.session.reference_instrument_type = None
        self._request_argument(
            f"reference:{action}",
            prompt,
            "输入 /back 或按 Esc 取消并返回当前菜单。",
        )
        return True

    def _dispatch_context(self, command: str, arguments: tuple[str, ...]) -> bool:
        if arguments:
            return False
        if not self.session.context:
            section = action_id(HOME_ACTIONS, command)
            if section is None:
                return False
            self.enter_section(section)
            return True

        section = self.session.context[0]
        if section == "market":
            return self._dispatch_market_context(command)
        if section == "reference":
            return self._dispatch_reference_context(command)
        if section == "strategy":
            return self._dispatch_strategy_context(command)
        if section == "resources":
            return self._dispatch_resource_context(command)
        if section == "research":
            return self._dispatch_research_context(command)
        if section == "operations":
            return self._dispatch_operations_context(command)
        return False

    def enter_section(self, section: str) -> None:
        """Enter one product context without replacing the command screen."""

        if section == "observe":
            self._run("observe", self._read_observe)
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

        record: dict[str, Any] = {"launch_id": launch_id}
        if source is not None:
            record["config"] = str(source)
            record["draft"] = True
        self.workbench_app.state.selected_launch = launch_id
        self.session.selected_launch_record = record
        self.session.context = ("strategy", "selected")
        self._write(
            Panel(
                (
                    f"已进入 Launch {launch_id} 的"
                    f"{'跟随输出' if action == 'attach' else '配置'}流程。"
                ),
                title="Launch 深链",
                border_style="cyan",
            )
        )
        if action == "attach":
            self.session.context = ("strategy", "attach")
            self.session.launch_attach_paused = False
            self.session.launch_attach_seen = ()
            self._show_context()
            self._refresh_launch_attach(force=True)
            return
        if action == "setup":
            try:
                wizard = LaunchWizardState.open(
                    launch_id, Path(str(source)) if source is not None else None
                )
            except (OSError, ValueError) as error:
                self._write_error(str(error))
                self._show_context()
            else:
                self._start_launch_wizard(wizard)
            return
        self._show_context()

    def action_back(self) -> None:
        if self.session.prompt_mode in {
            PromptMode.ARGUMENT,
            PromptMode.SECRET,
            PromptMode.CONFIRMATION,
        }:
            self.action_cancel_pending()
            return
        if not go_back(self.session):
            self._write(Text("当前已经在首页。", style="dim"))
            self._show_context()
            return
        self._show_context()

    def _request_argument(self, command: str, prompt: str, detail: str) -> None:
        self.session.ask(command)
        self._write(
            Panel(Group(Text(prompt, style="bold"), Text(detail)), title=command)
        )
        self._set_status(f"等待输入 · {command}")
        self._input().placeholder = prompt

    def _request_secret(self, command: str, prompt: str, detail: str) -> None:
        self.session.ask(command, secret=True)
        self._input().password = True
        self._write(
            Panel(Group(Text(prompt, style="bold"), Text(detail)), title=command)
        )
        self._set_status(f"等待安全输入 · {command}")
        self._input().placeholder = prompt

    def action_clear(self) -> None:
        self._output().clear()
        self._write(Text("输出已清空；业务状态没有改变。", style="dim"))
        self._show_context()

    def action_cancel_pending(self) -> None:
        argument_prompt = self.session.argument_prompt
        if argument_prompt is not None:
            command = argument_prompt.action
            self._input().password = False
            if command.startswith("research:"):
                self.session.research_action = None
                self.session.research_primary = None
            elif command.startswith("resource:"):
                self.session.resource_action = None
                self.session.resource_launch_id = None
                if command.startswith("resource:setup"):
                    self._cancel_resource_wizard()
            elif command.startswith("strategy:launch"):
                self._cancel_launch_wizard()
            elif command.startswith("business:field:"):
                self.session.business_prompt = None
            elif command.startswith("order:field:"):
                self.session.order_prompt = None
            elif command.startswith("execution:field:"):
                self.session.execution_prompt = None
            elif command.startswith("launch-market:field:"):
                self.session.launch_market_prompt = None
            elif command.startswith("market-file:field:"):
                self.session.market_file_prompt = None
            elif command.startswith("operations-project:field:"):
                self.session.project_prompt = None
            elif command.startswith("workspace-market:field:"):
                self.session.workspace_market_prompt = None
            elif command == "operations-profile:name":
                self.session.profile_action = None
            self.session.reset_prompt()
            self._input().placeholder = "输入命令；Enter 提交"
            self._write(Text(f"已取消 {command} 输入。", style="dim"))
            self._set_status("就绪")
            self._show_context()
            return
        confirmation_prompt = self.session.confirmation_prompt
        if confirmation_prompt is not None:
            summary = confirmation_prompt.summary
            result_kind = confirmation_prompt.result_kind
            self._interrupt_exit_pending = False
            if result_kind == "resource-wizard-result":
                self._cancel_resource_wizard()
            elif result_kind == "strategy-wizard-result":
                self._cancel_launch_wizard()
            elif result_kind == "order-result":
                self.session.order_prompt = None
            elif result_kind == "execution-result":
                self.session.execution_prompt = None
            elif result_kind == "launch-market-result":
                self.session.launch_market_prompt = None
            elif result_kind == "market-file-result":
                self.session.market_file_prompt = None
            elif result_kind == "operations-profile-result":
                self.session.profile_action = None
            elif result_kind == "operations-project-result":
                self.session.project_prompt = None
            elif result_kind == "workspace-market-result":
                self.session.workspace_market_prompt = None
            self.session.reset_prompt()
            self._write(Text(f"已取消：{summary}", style="dim"))
            self._set_status("就绪")
            self._show_context()
            return
        cancelled = self.workers.cancel_node(self)
        if cancelled:
            self._write(Text(f"已取消 {len(cancelled)} 个当前任务。", style="dim"))
            self._set_status("就绪")
            self.app.set_focus(self._input())
            return
        self._write(Text("当前没有可取消的输入或任务。", style="dim"))
        self._show_context()

    def action_interrupt(self) -> None:
        """Cancel active work, or ask before exiting when completely idle."""

        if self._interrupt_exit_pending:
            self.workbench_app.transcript.record(
                "session_finished", status="forced_interrupt"
            )
            self.app.exit(130)
            return
        if (
            self.session.prompt_mode
            in {PromptMode.ARGUMENT, PromptMode.SECRET, PromptMode.CONFIRMATION}
            or self._active_worker is not None
        ):
            self.action_cancel_pending()
            return
        self.request_confirmation(
            "当前没有运行中的任务，是否退出 Kairos Workbench？"
            "再次按 Ctrl+C 可强制退出。",
            self.workbench_app.action_quit,
        )
        self._interrupt_exit_pending = True

    def request_confirmation(
        self,
        summary: str,
        action: Callable[[], Any],
        *,
        result_kind: str = "confirmed",
    ) -> None:
        """Stage a dangerous action in the command stream, without a modal."""

        self._interrupt_exit_pending = False
        self.session.confirm(summary, action, result_kind)
        self.workbench_app.transcript.record(
            "confirmation_requested", screen=type(self).__name__, summary=summary
        )
        self._write(
            Panel(
                Text.from_markup(
                    f"{summary}\n\n输入 [bold]/confirm[/bold] 继续，输入 "
                    "[bold]/cancel[/bold] 或按 Esc 取消。"
                ),
                title="需要确认",
                border_style="yellow",
            )
        )
        self._set_status("等待确认")

    def _request_research_confirmation(
        self, action: str, value: str | None, extra: str | None
    ) -> None:
        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_research(action, value, extra)
            return execute_research(self.workbench_app.state, action, value, extra)

        self.session.research_action = None
        self.session.research_primary = None
        if (
            self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("research-result", operation)
        else:
            self.request_confirmation(
                f"kairos research {action} {value or ''}",
                operation,
                result_kind="research-result",
            )

    def _run_resource_action(
        self,
        action: str,
        *,
        value: str | None = None,
        launch_id: str | None = None,
    ) -> None:
        kind = self.session.resource_kind
        record = self.session.selected_resource
        if kind is None or record is None:
            self._write_error("资源上下文已经失效，请重新选择资源。")
            self.session.enter("resources")
            self._show_context()
            return

        def operation() -> Any:
            if action in {"test", "toggle", "delete", "attach", "detach"} and (
                self.workbench_app.state.dry_run or self.workbench_app.state.no_exec
            ):
                return preview_resource_action(
                    kind, record, action, value=value, launch_id=launch_id
                )
            return execute_resource_action(
                self.workbench_app.state,
                kind,
                record,
                action,
                value=value,
                launch_id=launch_id,
            )

        self._run(f"resource-action:{action}", operation)

    def _request_resource_confirmation(
        self,
        action: str,
        *,
        value: str | None = None,
        launch_id: str | None = None,
    ) -> None:
        kind = self.session.resource_kind
        record = self.session.selected_resource
        if kind is None or record is None:
            self._run_resource_action(action, value=value, launch_id=launch_id)
            return

        resource_id = resource_identity(kind, record)

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_resource_action(
                    kind, record, action, value=value, launch_id=launch_id
                )
            return execute_resource_action(
                self.workbench_app.state,
                kind,
                record,
                action,
                value=value,
                launch_id=launch_id,
            )

        self.session.resource_action = None
        self.session.resource_launch_id = None
        if (
            self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run(f"resource-action:{action}", operation)
        else:
            self.request_confirmation(
                f"kairos resource {action} {resource_id}",
                operation,
                result_kind=f"resource-action:{action}",
            )

    def _start_resource_wizard(self, wizard: ResourceWizardState) -> None:
        self.session.resource_wizard = wizard
        self.session.resource_kind = wizard.kind
        self.session.context = ("resources", "setup")
        self._show_context()
        self._write(
            Panel(
                f"{'修改' if wizard.editing else '添加'}运行资源。"
                "每次只填写一个字段；安全凭据会切换为遮罩输入。",
                title="资源配置向导",
                border_style="cyan",
            )
        )
        self._advance_resource_wizard()

    def _advance_resource_wizard(self) -> None:
        wizard = self.session.resource_wizard
        if not isinstance(wizard, ResourceWizardState):
            self._write_error("资源配置向导已经失效，请重新开始。")
            return
        prompt = wizard.next_prompt()
        if prompt is not None:
            name, label, detail, secret = prompt
            command = f"resource:setup-field:{name}"
            if secret:
                self._request_secret(command, label, detail)
            else:
                self._request_argument(command, label, detail)
            return
        self._write(
            Panel(
                Pretty(wizard.redacted_summary(), expand_all=True),
                title="资源配置脱敏摘要",
                border_style="cyan",
            )
        )

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return {
                    "status": "preview",
                    "action": "resource-save",
                    "summary": wizard.redacted_summary(),
                }
            return save_resource_wizard(self.workbench_app.state, wizard)

        if (
            self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("resource-wizard-result", operation)
        else:
            self.request_confirmation(
                f"保存{wizard.kind}运行资源",
                operation,
                result_kind="resource-wizard-result",
            )

    def _cancel_resource_wizard(self) -> None:
        wizard = self.session.resource_wizard
        self.session.resource_wizard = None
        if not isinstance(wizard, ResourceWizardState):
            return
        wizard.clear_secrets()
        if wizard.editing and self.session.selected_resource is not None:
            self.session.context = ("resources", "selected")
        else:
            self.session.context = ("resources", wizard.kind)
        self._write(Text("已取消资源配置向导；暂存凭据已清除。", style="dim"))

    def _advance_business_prompt(self) -> None:
        prompt = self.session.business_prompt
        if not isinstance(prompt, BusinessPromptState):
            self._write_error("业务工具参数向导已经失效，请重新选择操作。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"business:field:{name}", label, detail)
            return
        self._run(
            "business-result",
            lambda: execute_business(self.workbench_app.state, prompt),
        )

    def _advance_order_prompt(self) -> None:
        prompt = self.session.order_prompt
        if not isinstance(prompt, OrderPromptState):
            self._write_error("订单参数向导已经失效，请重新选择操作。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"order:field:{name}", label, detail)
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_order(prompt)
            return execute_order(self.workbench_app.state, prompt)

        if (
            not prompt.dangerous
            or self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("order-result", operation)
        else:
            self._write(
                Panel(
                    Pretty(prompt.summary(), expand_all=True),
                    title="订单作用域确认",
                    border_style="yellow",
                )
            )
            self.request_confirmation(
                f"{prompt.action} account={prompt.account_id}",
                operation,
                result_kind="order-result",
            )

    def _advance_execution_prompt(self) -> None:
        prompt = self.session.execution_prompt
        if not isinstance(prompt, ExecutionPromptState):
            self._write_error("Execution 参数向导已经失效，请重新选择操作。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"execution:field:{name}", label, detail)
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_connected_execution(prompt)
            return execute_connected_execution(self.workbench_app.state, prompt)

        if (
            not prompt.dangerous
            or self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("execution-result", operation)
        else:
            self._write(
                Panel(
                    Pretty(prompt.summary(), expand_all=True),
                    title="Execution 作用域确认",
                    border_style="yellow",
                )
            )
            self.request_confirmation(
                f"Execution {prompt.action} {prompt.launch_id}/{prompt.instance_id}",
                operation,
                result_kind="execution-result",
            )

    def _advance_launch_market_prompt(self) -> None:
        prompt = self.session.launch_market_prompt
        if not isinstance(prompt, LaunchMarketPromptState):
            self._write_error("Market 参数向导已经失效，请重新选择操作。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"launch-market:field:{name}", label, detail)
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_launch_market(prompt)
            return execute_launch_market(self.workbench_app.state, prompt)

        if (
            not prompt.dangerous
            or self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("launch-market-result", operation)
        else:
            self.request_confirmation(
                f"Market {prompt.action} {prompt.launch.get('launch_id')}/"
                f"{prompt.launch.get('instance_id')}",
                operation,
                result_kind="launch-market-result",
            )

    def _advance_market_file_prompt(self) -> None:
        prompt = self.session.market_file_prompt
        if not isinstance(prompt, MarketFilePromptState):
            self._write_error("Market 文件操作参数向导已经失效，请重新选择操作。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"market-file:field:{name}", label, detail)
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_market_file_action(prompt)
            return execute_market_file_action(self.workbench_app.state, prompt)

        self._write(
            Panel(
                Pretty(prompt.summary(), expand_all=True),
                title="Market 文件操作范围",
                border_style="yellow",
            )
        )
        if (
            self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("market-file-result", operation)
        else:
            self.request_confirmation(
                f"Market {prompt.action} {prompt.market.id}",
                operation,
                result_kind="market-file-result",
            )

    def _advance_project_prompt(self) -> None:
        prompt = self.session.project_prompt
        if not isinstance(prompt, ProjectPromptState):
            self._write_error("项目参数向导已经失效，请重新选择操作。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"operations-project:field:{name}", label, detail)
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return {"status": "preview", **prompt.summary()}
            return execute_operations_project_write(self.workbench_app.state, prompt)

        self._write(
            Panel(
                Pretty(prompt.summary(), expand_all=True),
                title="项目写入范围",
                border_style="yellow",
            )
        )
        if (
            self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("operations-project-result", operation)
        else:
            self.request_confirmation(
                f"项目操作 {prompt.action}",
                operation,
                result_kind="operations-project-result",
            )

    def _advance_workspace_market_prompt(self) -> None:
        prompt = self.session.workspace_market_prompt
        if not isinstance(prompt, WorkspaceMarketPromptState):
            self._write_error("Workspace Market 参数向导已经失效。")
            return
        next_prompt = prompt.next_prompt()
        if next_prompt is not None:
            name, label, detail = next_prompt
            self._request_argument(f"workspace-market:field:{name}", label, detail)
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return preview_workspace_market(prompt)
            return execute_workspace_market(self.workbench_app.state, prompt)

        if (
            not prompt.dangerous
            or self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("workspace-market-result", operation)
        else:
            self.request_confirmation(
                f"Workspace Market {prompt.action}",
                operation,
                result_kind="workspace-market-result",
            )

    def _start_launch_wizard(self, wizard: LaunchWizardState) -> None:
        self.session.launch_wizard = wizard
        self.session.context = ("strategy", "setup")
        self._show_context()
        self._write(
            Panel(
                f"开始配置 Launch {wizard.launch_id}。每次只填写一个字段；"
                "直接回车使用当前默认值。",
                title="Launch 配置向导",
                border_style="cyan",
            )
        )
        self._advance_launch_wizard()

    def _advance_launch_wizard(self) -> None:
        wizard = self.session.launch_wizard
        if not isinstance(wizard, LaunchWizardState):
            self._write_error("Launch 配置向导已经失效，请重新开始。")
            return
        prompt = wizard.next_prompt()
        if prompt is not None:
            name, label, detail = prompt
            self._request_argument(f"strategy:launch-field:{name}", label, detail)
            return
        self._write(
            Panel(wizard.preview(), title="Launch 脱敏摘要", border_style="cyan")
        )
        self._request_argument(
            "strategy:launch-save-mode",
            "保存方式（draft / publish）",
            "draft 仅保存草稿；publish 校验并发布。",
        )

    def _request_launch_wizard_confirmation(self, *, publish: bool) -> None:
        wizard = self.session.launch_wizard
        if not isinstance(wizard, LaunchWizardState):
            self._write_error("Launch 配置向导已经失效，请重新开始。")
            return

        def operation() -> Any:
            if self.workbench_app.state.dry_run or self.workbench_app.state.no_exec:
                return {
                    "status": "preview",
                    "action": "publish" if publish else "save-draft",
                    "launch_id": wizard.launch_id,
                    "summary": wizard.preview(),
                }
            return save_launch_wizard(self.workbench_app.state, wizard, publish=publish)

        if (
            self.workbench_app.state.yes
            or self.workbench_app.state.dry_run
            or self.workbench_app.state.no_exec
        ):
            self._run("strategy-wizard-result", operation)
        else:
            self.request_confirmation(
                f"{'发布' if publish else '保存草稿'} Launch {wizard.launch_id}",
                operation,
                result_kind="strategy-wizard-result",
            )

    def _cancel_launch_wizard(self) -> None:
        wizard = self.session.launch_wizard
        self.session.launch_wizard = None
        if self.session.selected_launch_record is not None:
            self.session.context = ("strategy", "selected")
        elif self.session.launch_records:
            self.session.context = ("strategy", "launches")
            self.session.visible_records = self.session.launch_records
        else:
            self.session.context = ("strategy",)
        if isinstance(wizard, LaunchWizardState):
            self._write(
                Text(f"已取消 Launch {wizard.launch_id} 配置向导。", style="dim")
            )

    def _confirm_pending(self) -> None:
        confirmation_prompt = self.session.confirmation_prompt
        if confirmation_prompt is None:
            self._write(Text("当前没有等待确认的操作。", style="dim"))
            return
        summary = confirmation_prompt.summary
        action = confirmation_prompt.operation
        result_kind = confirmation_prompt.result_kind
        self._interrupt_exit_pending = False
        self.session.finish_prompt()
        self.workbench_app.transcript.record(
            "confirmation_accepted", screen=type(self).__name__, summary=summary
        )
        self._run(result_kind, action, status=f"正在执行：{summary}")

    def _run(
        self,
        kind: str,
        operation: Callable[[], Any],
        *,
        status: str | None = None,
    ) -> None:
        self.session.busy(kind)
        self._input().disabled = True
        self._set_status(status or _running_status(kind))
        self._active_worker = self.run_worker(
            operation,
            name=f"command-{kind}",
            group="guided-command",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _read_observe(self) -> ObserveSnapshot | None:
        return self.workbench_app.state.refresh_snapshot()

    def _record_action(
        self,
        action: str,
        arguments: tuple[str, ...],
        *,
        equivalent_command: tuple[str, ...] | None = None,
    ) -> None:
        self.workbench_app.transcript.record(
            "action",
            screen=type(self).__name__,
            action=action,
            arguments=list(arguments),
            equivalent_command=(
                shlex.join(equivalent_command)
                if equivalent_command is not None
                else None
            ),
        )

    def _observe_command(self) -> tuple[str, ...]:
        state = self.workbench_app.state
        command = ["kairos", "observe"]
        if state.workspace_arg is not None:
            command.extend(("--workspace", str(state.workspace_arg)))
        command.append("--once")
        return tuple(command)

    def _find_markets(self, query: str) -> tuple[Any, ...]:
        return load_reference_records(
            self.workbench_app.state,
            "markets",
            query,
        )

    def _find_reference_records(self, kind: str, query: str) -> tuple[Any, ...]:
        return load_reference_records(
            self.workbench_app.state,
            kind,
            query,
            instrument_type=self.session.reference_instrument_type,
        )

    def _request_market_observation(self, provider: str) -> None:
        market = self.workbench_app.state.selected_market
        observation = self.session.market_observation
        if market is None or observation is None:
            self._write_error("行情上下文已经失效，请重新选择标的。")
            self.session.enter("market")
            self._show_context()
            return
        self._run(
            "market-observation",
            lambda: load_market_observation(
                self.workbench_app.state,
                market,
                observation,
                provider,
            ),
            status=f"正在通过 {provider} 读取行情…",
        )

    def _refresh_launch_attach(self, *, force: bool = False) -> None:
        if self.session.context != ("strategy", "attach"):
            return
        if self.session.launch_attach_paused and not force:
            return
        if self._attach_refresh_worker is not None:
            return
        record = self.session.selected_launch_record
        if record is None:
            return
        launch_id = str(record["launch_id"])
        self._attach_refresh_worker = self.run_worker(
            lambda: load_launch_attach_snapshot(self.workbench_app.state, launch_id),
            name="launch-attach-stream",
            group="launch-attach-stream",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _render_launch_attach_snapshot(self, result: Any) -> None:
        if not isinstance(result, Mapping):
            self._write(Panel(Pretty(result, expand_all=True), title="Launch 运行输出"))
            return
        runtime = result.get("status")
        instance = result.get("instance")
        self._write(
            Panel(
                Pretty({"instance": instance, "status": runtime}, expand_all=True),
                title="Launch 状态刷新",
                border_style="cyan",
            )
        )
        logs = result.get("logs")
        raw_lines = logs.get("lines", ()) if isinstance(logs, Mapping) else ()
        lines = tuple(str(line) for line in raw_lines)
        seen = self.session.launch_attach_seen
        overlap = 0
        for size in range(min(len(seen), len(lines)), 0, -1):
            if seen[-size:] == lines[:size]:
                overlap = size
                break
        for line in lines[overlap:]:
            self._write(Text(line))
        self.session.launch_attach_seen = lines

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
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
                self.session.launch_attach_paused = True
                if self.session.context == ("strategy", "attach"):
                    self._write_error(str(event.worker.error))
                    self._set_status("跟随输出失败 · 已暂停")
                    self._write_next_step("输入 1 重试，/p 继续，/back 返回。")
            elif event.state.name == "CANCELLED":
                self._attach_refresh_worker = None
            return
        if event.worker.group != "guided-command":
            return
        if event.worker is not self._active_worker:
            return
        kind = parse_result_kind(event.worker.name.removeprefix("command-"))
        if event.state.name == "SUCCESS":
            self._set_status("就绪")
            self._active_worker = None
            self._restore_navigation_input()
            self._render_result(kind, event.worker.result)
        elif event.state.name == "ERROR":
            self._write_error(str(event.worker.error))
            self._set_status("失败 · 可继续输入")
            self._active_worker = None
            self._restore_navigation_input()
            self._clear_terminal_flow(kind)
            self._write_next_step("操作失败；可重新输入，/back 返回，/help 查看帮助。")
        elif event.state.name == "CANCELLED":
            self._set_status("已取消 · 可继续输入")
            self._active_worker = None
            self._restore_navigation_input()
            self._clear_terminal_flow(kind)
            self._write_next_step("操作已取消；可继续输入。")

    def _clear_terminal_flow(self, kind: ResultKey) -> None:
        if kind is ResultKind.RESOURCE_WIZARD:
            self._cancel_resource_wizard()
        elif kind is ResultKind.STRATEGY_WIZARD:
            self._cancel_launch_wizard()
        else:
            self.session.clear_result_flow(str(kind))

    def _restore_navigation_input(self) -> None:
        self.session.finish_prompt()
        self._input().disabled = False
        self._input().password = False
        self.app.set_focus(self._input())
        self.call_after_refresh(self.app.set_focus, self._input())

    def _render_market_reference_result(
        self, kind: ResultKey | None, result: Any
    ) -> bool:
        if not (
            kind is not None
            and (
                kind == ResultKind.MARKET
                or kind.startswith("reference:")
                or kind
                in {
                    ResultKind.REFERENCE_RELATED,
                    ResultKind.MARKET_ROUTES,
                    ResultKind.MARKET_OBSERVATION,
                    ResultKind.MARKET_DATASETS,
                }
            )
        ):
            return False

        if kind == ResultKind.MARKET:
            records = tuple(result or ())
            self.session.market_records = records
            self._write(_markets_renderable(records, numbered=True))
            self._show_record_choices("market", records)
        elif kind is not None and kind.startswith("reference:"):
            records = tuple(result or ())
            reference_kind = kind.partition(":")[2]
            self._write(reference_records_renderable(reference_kind, records))
            self._show_record_choices("reference", records, kind=reference_kind)
        elif kind == "reference-related":
            related_kind, records = result
            self._write(reference_records_renderable(related_kind, tuple(records)))
            self._show_context()
        elif kind == "market-routes":
            routes = tuple(dict(route) for route in (result or ()))
            self.session.market_routes = routes
            market = self.workbench_app.state.selected_market
            observation = self.session.market_observation or "quote"
            if market is None:
                self._write_error("行情上下文已经失效，请重新选择标的。")
                self.session.enter("market")
                self._show_context()
            elif not routes:
                self._write(
                    route_diagnostic_renderable(
                        self.workbench_app.state,
                        market,
                        observation,
                    )
                )
                self.session.context = ("market", "selected")
                self.session.visible_records = self.session.market_records
                self._show_context()
            elif len(routes) == 1:
                self._request_market_observation(str(routes[0].get("provider") or ""))
            else:
                self.session.context = ("market", "providers")
                self.session.visible_records = routes
                self._show_context()
        elif kind == "market-observation":
            self._write(market_observation_renderable(result))
            self.session.context = ("market", "selected")
            self.session.visible_records = self.session.market_records
            self._show_context()
        elif kind == "market-datasets":
            self._write(Panel(Pretty(result, expand_all=True), title="本地行情数据"))
            self._show_context()
        return True

    def _render_operations_result(self, kind: ResultKey | None, result: Any) -> bool:
        if kind not in {
            ResultKind.KAIROS_COMMAND,
            ResultKind.OPERATIONS_SERVICES,
            ResultKind.OPERATIONS,
            ResultKind.OPERATIONS_PROJECT,
            ResultKind.OPERATIONS_PROFILE,
            ResultKind.BUSINESS,
            ResultKind.ACCOUNT,
            ResultKind.ORDER,
            ResultKind.EXECUTION,
            ResultKind.LAUNCH_MARKET,
            ResultKind.MARKET_FILE,
            ResultKind.WORKSPACE_MARKET,
        }:
            return False

        if kind == ResultKind.KAIROS_COMMAND:
            self._write(Panel(Pretty(result, expand_all=True), title="kairos 命令结果"))
            self._show_context()
        elif kind == "operations-services":
            records = tuple(result or ())
            self._write(_services_renderable(records))
            self._show_record_choices("operations", records, kind="services")
        elif kind == "operations-result":
            self._write(Panel(Pretty(result, expand_all=True), title="系统维护结果"))
            self._show_context()
        elif kind == "operations-project-result":
            self._write(Panel(Pretty(result, expand_all=True), title="项目操作结果"))
            self.session.project_prompt = None
            self.session.context = ("operations", "project")
            self._show_context()
        elif kind == "operations-profile-result":
            self._write(
                Panel(Pretty(result, expand_all=True), title="Profile 操作结果")
            )
            self.session.profile_action = None
            self.session.context = ("operations", "profiles")
            self._show_context()
        elif kind == "business-result":
            self._write(Panel(Pretty(result, expand_all=True), title="业务工具结果"))
            self.session.business_prompt = None
            self._show_context()
        elif kind == "account-result":
            self._write(Panel(Pretty(result, expand_all=True), title="账户运行结果"))
            self._show_context()
        elif kind == "order-result":
            self._write(Panel(Pretty(result, expand_all=True), title="订单操作结果"))
            self.session.order_prompt = None
            self._show_context()
        elif kind == "execution-result":
            self._write(Panel(Pretty(result, expand_all=True), title="Execution 结果"))
            self.session.execution_prompt = None
            self._show_context()
        elif kind == "launch-market-result":
            self._write(Panel(Pretty(result, expand_all=True), title="Market 组件结果"))
            self.session.launch_market_prompt = None
            self._show_context()
        elif kind == "market-file-result":
            self._write(
                Panel(Pretty(result, expand_all=True), title="Market 文件操作结果")
            )
            self.session.market_file_prompt = None
            self.session.context = ("market", "selected")
            self.session.visible_records = self.session.market_records
            self._show_context()
        elif kind == "workspace-market-result":
            self._write(
                Panel(Pretty(result, expand_all=True), title="Workspace Market 结果")
            )
            self.session.workspace_market_prompt = None
            self.session.context = ("market", "connected")
            self._show_context()
        return True

    def _render_resource_result(self, kind: ResultKey | None, result: Any) -> bool:
        if not (
            kind is not None
            and (
                kind in {ResultKind.RESOURCES_SUMMARY, ResultKind.RESOURCE_WIZARD}
                or kind.startswith("resources-list:")
                or kind.startswith("resource-action:")
            )
        ):
            return False

        if kind == ResultKind.RESOURCES_SUMMARY:
            self._write(resource_summary_renderable(result))
            self._show_context()
        elif kind is not None and kind.startswith("resources-list:"):
            resource_kind = kind.partition(":")[2]
            records = tuple(dict(record) for record in (result or ()))
            self.session.resource_kind = resource_kind
            self._write(resource_records_renderable(resource_kind, records))
            self._show_record_choices("resources", records, kind=resource_kind)
        elif kind is not None and kind.startswith("resource-action:"):
            action = kind.partition(":")[2]
            self._write(Panel(Pretty(result, expand_all=True), title="资源操作结果"))
            if action == "delete":
                selected = self.session.selected_resource
                resource_kind = self.session.resource_kind
                if selected is not None and resource_kind is not None:
                    selected_id = resource_identity(resource_kind, selected)
                    self.session.visible_records = tuple(
                        record
                        for record in self.session.visible_records
                        if resource_identity(resource_kind, record) != selected_id
                    )
                self.session.selected_resource = None
                self.session.context = ("resources",)
            elif isinstance(result, Mapping) and any(
                key in result
                for key in ("account_id", "connection_id", "destination_id")
            ):
                self.session.selected_resource = dict(result)
            self._show_context()
        elif kind == "resource-wizard-result":
            wizard = self.session.resource_wizard
            self._write(Panel(Pretty(result, expand_all=True), title="资源配置结果"))
            if isinstance(wizard, ResourceWizardState):
                preview = (
                    isinstance(result, Mapping) and result.get("status") == "preview"
                )
                if preview:
                    self.session.selected_resource = None
                    self.session.context = ("resources", wizard.kind)
                else:
                    selected = dict(result) if isinstance(result, Mapping) else {}
                    self.session.selected_resource = selected
                    self.session.context = ("resources", "selected")
                    if wizard.kind == "accounts" and selected:
                        self.workbench_app.state.selected_account = resource_identity(
                            wizard.kind, selected
                        )
                wizard.clear_secrets()
            self.session.resource_wizard = None
            self._show_context()
        return True

    def _render_strategy_result(self, kind: ResultKey | None, result: Any) -> bool:
        if not (kind is not None and kind.startswith("strategy")):
            return False

        if kind == ResultKind.STRATEGY_LAUNCHES:
            records = tuple(dict(record) for record in (result or ()))
            self.session.launch_records = records
            self._write(launch_records_renderable(records))
            self._show_record_choices("strategy", records, kind="launches")
        elif kind == "strategy-instances":
            records = tuple(dict(record) for record in (result or ()))
            self.session.launch_instance_records = records
            self._write(launch_instances_renderable(records))
            self._show_record_choices("strategy", records, kind="instances")
        elif kind == "strategy-components":
            records = tuple(dict(record) for record in (result or ()))
            self.session.launch_component_records = records
            self._write(launch_components_renderable(records))
            self._show_record_choices("strategy", records, kind="components")
        elif kind == "strategy-instance-result":
            self._write(Panel(Pretty(result, expand_all=True), title="实例概览"))
            self._show_context()
        elif kind == "strategy-timeline":
            records = tuple(result or ())
            self._write(
                Panel(
                    Pretty(records, expand_all=True),
                    title=f"实例时间线 · {len(records)} 条",
                )
            )
            self.session.context = ("strategy", "timeline")
            self._show_context()
        elif kind == "strategy-timeline-export":
            self._write(Panel(Pretty(result, expand_all=True), title="时间线导出结果"))
            self.session.context = ("strategy", "timeline")
            self._show_context()
        elif kind == "strategy-attach":
            self._write(Panel(Pretty(result, expand_all=True), title="Launch 运行输出"))
            self.session.context = ("strategy", "attach")
            self._show_context()
        elif kind == "strategy-result":
            self._write(Panel(Pretty(result, expand_all=True), title="Launch 结果"))
            self._show_context()
        elif kind == "strategy-wizard-result":
            wizard = self.session.launch_wizard
            self._write(Panel(Pretty(result, expand_all=True), title="Launch 配置结果"))
            if isinstance(wizard, LaunchWizardState):
                record = {
                    "launch_id": wizard.launch_id,
                    "config": str(
                        result.get("path")
                        if isinstance(result, Mapping) and result.get("path")
                        else wizard.source or ""
                    ),
                    "draft": not (
                        isinstance(result, Mapping)
                        and result.get("status") == "published"
                    ),
                    "mode": wizard.answers.get("mode"),
                }
                self.session.selected_launch_record = record
                self.workbench_app.state.selected_launch = wizard.launch_id
            self.session.launch_wizard = None
            self.session.context = ("strategy", "selected")
            self._show_context()
        return True

    def _render_result(self, kind: ResultKey | None, result: Any) -> None:
        if isinstance(kind, str):
            kind = parse_result_kind(kind)
        if kind is ResultKind.OBSERVE:
            self._write(
                Text("当前没有可用的系统观察结果。", style="dim")
                if result is None
                else _observe_renderable(result)
            )
        elif self._render_market_reference_result(kind, result):
            pass
        elif self._render_operations_result(kind, result):
            pass
        elif self._render_resource_result(kind, result):
            pass
        elif kind == "research-result":
            self._write(Panel(Pretty(result, expand_all=True), title="数据研究结果"))
            self._show_context()
        elif self._render_strategy_result(kind, result):
            pass
        elif kind is not None and kind.startswith("market-diagnostic:"):
            self._write(Panel(Pretty(result, expand_all=True), title="Market 诊断"))
            self.session.context = ("market", "selected")
            self.session.visible_records = self.session.market_records
            self._show_context()
        else:
            self._write(Panel(str(result), title="完成", border_style="green"))

    def _write_welcome(self) -> None:
        state = self.workbench_app.state
        self._write(
            Group(
                Text("Kairos Workbench", style="bold cyan"),
                Text(f"Workspace: {state.workspace_id}", style="dim"),
                Text("交易，从这里开始", style="bold"),
                Text("输入 1–6 选择；每一步都继续使用下方输入框。", style="dim"),
                Text(
                    "输入 /xxx 控制 Workbench；其他文本按 kairos <输入> 执行。",
                    style="dim",
                ),
            )
        )

    def _show_context(self) -> None:
        if self.session.prompt_mode is PromptMode.NAVIGATION:
            self._input().disabled = False
        items = context_items(self.session, self.workbench_app.state)
        actions = self.query_one("#guided-actions", GuidedActionList)
        actions.replace_items(items)
        actions.display = bool(items)
        context = context_label(self.session.context)
        self.query_one("#command-context", Static).update(f"{context}  ›")
        self._input().placeholder = "输入编号或命令；Enter 提交"
        self._set_status(f"{context} · 等待输入")
        self.app.set_focus(self._input())
        self.call_after_refresh(self.app.set_focus, self._input())

    def _show_record_choices(
        self,
        section: str,
        records: tuple[Any, ...],
        *,
        kind: str | None = None,
    ) -> None:
        self.session.context = (section, kind or "results")
        self.session.visible_records = records
        items = tuple(
            ActionItem(
                str(index),
                record_label(record),
                record_description(record),
                str(index),
            )
            for index, record in enumerate(records, 1)
        )
        actions = self.query_one("#guided-actions", GuidedActionList)
        actions.replace_items(items)
        actions.display = bool(items)
        context = context_label(self.session.context)
        self.query_one("#command-context", Static).update(f"{context}  ›")
        self._set_status(f"{context} · 请选择结果")
        self._write_next_step("输入结果编号查看详情；/back 返回当前菜单。")
        self.app.set_focus(self._input())
        self.call_after_refresh(self.app.set_focus, self._input())

    def _write_next_step(self, value: str) -> None:
        self._write(Text(value, style="dim"))

    def _write_prompt(self, value: str) -> None:
        prompt = Text("kairos › ", style="bold bright_blue")
        prompt.append(value)
        self._write(prompt)

    def _write_error(self, value: str) -> None:
        self._write(Panel(value, title="命令失败", border_style="red"))

    def _write(self, renderable: RenderableType) -> None:
        self._output().write(renderable)

    def _output(self) -> RichLog:
        return self.query_one("#command-output", RichLog)

    def _input(self) -> WorkbenchCommandInput:
        return self.query_one("#command-input", WorkbenchCommandInput)

    def _set_status(self, value: str) -> None:
        self.query_one("#command-status", Static).update(value)


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


def _help_renderable(context: tuple[str, ...] = ()) -> RenderableType:
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
    table.add_row("/clear", "清空当前输出显示")
    table.add_row("/transcript", "显示当前 Agent 可读会话记录的路径")
    table.add_row("/copy", "复制当前页完整输出，可直接粘贴给 Agent")
    table.add_row("/confirm /cancel", "继续或取消等待中的步骤")
    table.add_row("/help", "显示这份帮助")
    return Panel(
        table,
        title=f"{context_label(context)} · 帮助",
        border_style="cyan",
    )


def _record_choice(records: tuple[Any, ...], value: str) -> Any | None:
    try:
        index = int(value)
    except ValueError:
        return None
    return records[index - 1] if 1 <= index <= len(records) else None


def _record_detail_renderable(record: Any, *, section: str) -> RenderableType:
    if is_dataclass(record):
        value = {field.name: getattr(record, field.name) for field in fields(record)}
    elif hasattr(record, "__dict__"):
        value = vars(record)
    else:
        value = record
    return Panel(
        Pretty(value, expand_all=True),
        title=f"{record_label(record)} · {'行情标的' if section == 'market' else 'Reference'}",
        border_style="cyan",
    )


def _observe_renderable(snapshot: ObserveSnapshot) -> RenderableType:
    table = Table(show_header=True, header_style="bold")
    table.add_column("组件")
    table.add_column("状态")
    table.add_column("新鲜度")
    table.add_column("详情")
    for row in component_rows(snapshot):
        table.add_row(*(str(value) for value in row))
    summary = Text(
        f"{snapshot.workspace_id} · {snapshot.overall_status} · "
        f"{len(snapshot.launches)} 个 Launch\n",
        style="bold",
    )
    summary.append(f"下一步：{recommended_action(snapshot)}", style="dim")
    return Panel(Group(summary, table), title="系统状态", border_style="cyan")


def _markets_renderable(
    markets: tuple[Any, ...], *, numbered: bool = False
) -> RenderableType:
    if not markets:
        return Panel("没有找到匹配的有效标的。", title="市场搜索")
    table = Table(show_header=True, header_style="bold")
    if numbered:
        table.add_column("#", justify="right", style="bold cyan")
    table.add_column("代码")
    table.add_column("交易所")
    table.add_column("类型")
    table.add_column("计价")
    table.add_column("状态")
    for index, market in enumerate(markets, 1):
        row = (
            str(market.venue_symbol or market.instrument.display_symbol),
            str(market.exchange_id).rsplit(":", 1)[-1],
            str(market.instrument_kind),
            str(market.quote_asset or "—"),
            str(market.status),
        )
        table.add_row(str(index), *row) if numbered else table.add_row(*row)
    return Panel(table, title=f"找到 {len(markets)} 个标的", border_style="cyan")


def _reference_records_renderable(
    kind: str, records: tuple[Any, ...]
) -> RenderableType:
    titles = {
        "assets": "资产",
        "exchanges": "交易所",
        "instruments": "合约",
        "markets": "交易标的",
        "option-chain": "期权链",
    }
    if not records:
        return Panel("没有找到匹配的记录。", title=titles.get(kind, "Reference"))
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("名称")
    table.add_column("说明")
    table.add_column("ID", style="dim")
    for index, record in enumerate(records, 1):
        table.add_row(
            str(index),
            record_label(record),
            record_description(record),
            str(getattr(record, "id", "—")),
        )
    return Panel(
        table,
        title=f"找到 {len(records)} 条{titles.get(kind, 'Reference')}记录",
        border_style="cyan",
    )


def _services_renderable(records: tuple[dict[str, Any], ...]) -> RenderableType:
    if not records:
        return Panel("当前没有 Workspace 服务记录。", title="系统服务")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("组件")
    table.add_column("状态")
    table.add_column("PID")
    table.add_column("详情")
    for index, record in enumerate(records, 1):
        table.add_row(
            str(index),
            str(record.get("component") or "—"),
            str(record.get("status") or "unknown"),
            str(record.get("pid") or "—"),
            str(record.get("error") or record.get("detail") or "—"),
        )
    return Panel(table, title=f"{len(records)} 个 Workspace 服务", border_style="cyan")


def _running_status(kind: str) -> str:
    return {
        "observe": "正在读取系统状态…",
        "market": "正在搜索市场标的…",
    }.get(kind, "正在执行…")
