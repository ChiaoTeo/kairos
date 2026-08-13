from __future__ import annotations

import json
import codeop
from contextlib import redirect_stdout
from io import StringIO
import sys
import time
from pathlib import Path
from typing import Any

import typer

from kairospy.application.market import read_replay_events
from kairospy.application.launch.application import (
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchControlApplication,
    LaunchInstanceTimelineApplication,
    LaunchRegistryApplication,
    LaunchRuntimeApplication,
    LaunchRuntimeError,
)
from kairospy.application.launch.application.runtime import (
    acquire_launch_leases as _acquire_launch_leases,
    cleanup_instance_components as _cleanup_instance_components,
    release_launch_leases as _release_launch_leases,
    requires_reference_runtime as _requires_reference_runtime,
    stop_component_safely as _stop_component_safely,
)
from kairospy.application.launch.application.wizard import (
    build_and_validate,
    draft_preview,
    load_values,
    prompt_draft,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.system import UnixRestClient
from kairospy.surface.cli.options import (
    OutputFormat,
    effective_output,
    render,
    reset_command_output,
    set_command_output,
)


launch_app = typer.Typer(no_args_is_help=True, help="Manage launch instances")
strategy_app = typer.Typer(
    no_args_is_help=True, help="Manage the strategy inside a launch instance"
)
launch_app.add_typer(strategy_app, name="strategy")
instance_app = typer.Typer(no_args_is_help=True, help="Inspect a launch instance")
instance_timeline_app = typer.Typer(
    no_args_is_help=True, help="Inspect lifecycle records from one launch instance"
)
launch_app.add_typer(instance_app, name="instance")
instance_app.add_typer(instance_timeline_app, name="timeline")


@launch_app.command(
    "init", help="Create a launch configuration with an interactive wizard."
)
def init_launch(
    launch_id: str | None = typer.Argument(
        None, help="Launch id (prompted when omitted)."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_launch_id = launch_id or typer.prompt("Launch id", default="new-launch")
    path = owner.paths.launch_config(resolved_launch_id)
    if path.exists():
        raise typer.BadParameter(f"launch config already exists: {path}")
    draft = prompt_draft(default_launch_id=resolved_launch_id)
    values = draft.apply({})
    typer.echo(draft_preview(values))
    if not typer.confirm("保存并创建 launch 配置", default=True):
        _emit({"status": "cancelled", "path": str(path)}, output)
        return
    try:
        report = build_and_validate(path, values, owner.paths.root)
    except LaunchConfigError as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"status": "created", "path": str(path), "validation": report}, output)


@launch_app.command(
    "edit", help="Edit a launch configuration with an interactive wizard."
)
def edit_launch(
    launch_id: str = typer.Argument(..., help="Launch id or launch TOML path."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        path = _launch_config_path(owner, launch_id)
        values = load_values(path)
        draft = prompt_draft(values, default_launch_id=path.stem)
        updated = draft.apply(values)
        typer.echo(draft_preview(updated))
        if not typer.confirm("保存 launch 配置", default=True):
            _emit({"status": "cancelled", "path": str(path)}, output)
            return
        report = build_and_validate(path, updated, owner.paths.root)
    except (FileNotFoundError, LaunchConfigError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"status": "updated", "path": str(path), "validation": report}, output)


def _group(name: str, commands: tuple[str, ...]) -> typer.Typer:
    descriptions = {
        "targets": "Manage reusable launch targets.",
        "diagnose": "Validate and explain launch configuration.",
        "replay": "Inspect replay input and progress.",
    }
    group = typer.Typer(
        no_args_is_help=True, help=descriptions.get(name, f"Launch {name} commands")
    )
    launch_app.add_typer(group, name=name)
    del commands
    return group


targets_app = _group("targets", ("add", "remove", "index", "list", "browse"))
diagnose_app = _group("diagnose", ("validate", "explain"))
replay_app = _group("replay", ("events",))


def _target(launch_id: str, instance: str, mode: str, workspace: Path):
    value = WorkspaceApplication().open(workspace)
    return LaunchControlApplication(value).target(launch_id, instance, mode=mode)


def _running_instance(owner, launch_id: str, mode: str | None = None) -> dict | None:
    try:
        return LaunchRuntimeApplication(owner).running_instance(launch_id, mode)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _resolve_launch_target(
    owner,
    launch_id: str,
    mode: str | None,
    instance: str | None,
) -> tuple[str, str]:
    try:
        return LaunchRuntimeApplication(owner).resolve_target(
            launch_id, mode=mode, instance=instance
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _resolve_instance(owner, launch_id: str, mode: str, instance: str | None) -> str:
    """Backward-compatible instance-only resolver for callers with a mode."""
    return _resolve_launch_target(owner, launch_id, mode, instance)[0]


def _decorate_launch_status(
    owner, launch_id: str, instance: str, mode: str, value: dict
) -> dict:
    return LaunchRuntimeApplication(owner).decorate_status(
        launch_id, instance, mode, value
    )


def _resolve_stop_instance(
    owner,
    launch_id: str,
    instance: str | None,
    mode: str | None,
) -> tuple[str, str]:
    try:
        return LaunchRuntimeApplication(owner).resolve_stop_target(
            launch_id, instance=instance, mode=mode
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _launch_config_path(owner, target: str | Path) -> Path:
    candidate = Path(target).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.is_file():
        return candidate
    configured = owner.paths.launch_config(str(target))
    if configured.is_file():
        return configured
    raise FileNotFoundError(
        f"launch {target!s} has no configuration; expected {configured}. "
        "Run 'kairos project doctor' to inspect project readiness."
    )


@launch_app.command(
    "start", help="Start a configured strategy launch and its dependencies."
)
def start(
    launch_id: str | None = typer.Argument(None),
    strategy: str | None = typer.Option(
        None, "--strategy", help="Strategy import path: module:callable"
    ),
    config: Path | None = typer.Option(
        None, "--config", help="Launch TOML configuration path."
    ),
    params: str | None = typer.Option(
        None, "--params", help="JSON object passed to the strategy factory"
    ),
    account_id: list[str] = typer.Option(
        [], "--account-id", help="Account binding to lease; repeatable."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    config_path: Path | None = config
    if config_path is None and launch_id is not None:
        try:
            config_path = _launch_config_path(owner, launch_id)
        except (FileNotFoundError, ValueError) as error:
            raise typer.BadParameter(str(error), param_hint="launch_id") from error
    if config_path is None:
        raise typer.BadParameter(
            "launch TOML config is required; pass --config or use "
            "config/launches/<launch-id>.toml"
        )
    try:
        launch_config = LaunchConfigurationApplication().load(
            config_path, workspace_root=owner.paths.root
        )
        launch_config.require_valid()
    except LaunchConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--config") from error
    positional_config = launch_id is not None and Path(launch_id).expanduser().is_file()
    if (
        launch_id is not None
        and not positional_config
        and launch_id != launch_config.launch_id
    ):
        raise typer.BadParameter("launch id does not match launch config")
    if strategy is not None and strategy != launch_config.strategy:
        raise typer.BadParameter("--strategy does not match launch config")
    overrides: dict[str, Any] = {}
    if params:
        try:
            value = json.loads(params)
        except json.JSONDecodeError as error:
            raise typer.BadParameter("--params must be a JSON object") from error
        if not isinstance(value, dict):
            raise typer.BadParameter("--params must be a JSON object")
        overrides = value
    try:
        value = LaunchRuntimeApplication(owner).start(
            launch_config,
            strategy_params=overrides,
            account_ids=tuple(account_id),
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("status", help="Show aggregate strategy and dependency health.")
def status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).status(launch_id, instance=instance)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("report")
def report(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read the immutable report emitted when a backtest replay completes."""
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).report(launch_id, instance=instance)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("wait")
def wait(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Wait for a backtest replay, tear down runtime actors, and return its report."""
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).wait(
            launch_id, instance=instance, timeout=timeout
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("stop", help="Stop a launch and release its runtime resources.")
def stop(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).stop(launch_id, instance=instance)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command(
    "restart", help="Stop and start a launch with a new runtime instance."
)
def restart(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Restart a launch without duplicating its start/stop lifecycle logic."""
    stop_output = StringIO()
    output_token = set_command_output(OutputFormat.JSON)
    try:
        with redirect_stdout(stop_output):
            stop(
                launch_id,
                instance=instance,
                workspace=workspace,
                output=OutputFormat.JSON,
            )
    finally:
        reset_command_output(output_token)
    try:
        stopped = json.loads(stop_output.getvalue())
    except json.JSONDecodeError as error:
        raise typer.BadParameter("launch stop did not return a valid result") from error
    if stopped.get("status") != "stopped":
        issues = stopped.get("stop_issues") or {}
        raise typer.BadParameter(
            f"launch {launch_id} was not fully stopped; restart aborted: {issues}"
        )
    start(
        launch_id,
        strategy=None,
        config=None,
        params=None,
        account_id=[],
        workspace=workspace,
        output=output,
    )


@strategy_app.command("status")
def strategy_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        LaunchControlApplication(owner).status(
            _target(launch_id, resolved_instance, mode, workspace)
        ),
        output,
    )


def _strategy_action(action: str):
    def command(
        launch_id: str,
        instance: str | None = typer.Option(None, "--instance"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        resolved_instance, mode = _resolve_launch_target(
            owner, launch_id, None, instance
        )
        target = _target(launch_id, resolved_instance, mode, workspace)
        _emit(
            LaunchControlApplication(owner).strategy_control(target, action),
            output,
        )

    command.__name__ = f"strategy_{action}"
    return command


for _action in ("enable", "pause", "resume", "refresh"):
    strategy_app.command(_action)(_strategy_action(_action))


def _registry_command(action: str):
    def command(
        launch_id: str | None = typer.Argument(None),
        instance: str = typer.Option("default", "--instance"),
        mode: str = typer.Option("paper", "--mode"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = LaunchRegistryApplication(WorkspaceApplication().open(workspace))
        if action in {"list", "browse", "index"}:
            value = (
                app.instances(launch_id)
                if action != "index"
                else {"path": str(app.path), "instances": app.list()}
            )
        elif action == "add":
            if not launch_id:
                raise typer.BadParameter("launch_id is required")
            config_path = Path(launch_id).expanduser()
            if config_path.is_file():
                try:
                    config = LaunchConfigurationApplication().load(
                        config_path, workspace_root=app.workspace.paths.root
                    )
                    config.require_valid()
                except LaunchConfigError as error:
                    raise typer.BadParameter(str(error)) from error
                value = app.add(
                    config.launch_id,
                    mode=config.mode,
                    instance_id=instance,
                    strategy_ref=config.strategy,
                    config_path=config.path,
                )
            else:
                value = app.add(launch_id, mode=mode, instance_id=instance)
        elif action == "remove":
            if not launch_id:
                raise typer.BadParameter("launch_id is required")
            value = app.remove(launch_id, mode=mode, instance_id=instance)
        else:
            value = app.list()
        _emit(value, output)

    command.__name__ = f"launch_target_{action}"
    return command


for _action in ("add", "remove", "index", "list", "browse"):
    targets_app.command(_action)(_registry_command(_action))


def _diagnose(action: str):
    def command(
        launch_id: str,
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        try:
            config_path = _launch_config_path(owner, launch_id)
        except FileNotFoundError as error:
            raise typer.BadParameter(str(error), param_hint="launch_id") from error
        application = LaunchConfigurationApplication()
        try:
            value = (
                application.validate(config_path, workspace_root=owner.paths.root)
                if action == "validate"
                else application.explain(config_path, workspace_root=owner.paths.root)
            )
        except LaunchConfigError as error:
            raise typer.BadParameter(str(error)) from error
        _emit(value, output)

    command.__name__ = f"launch_diagnose_{action}"
    return command


for _action in ("validate", "explain"):
    diagnose_app.command(_action)(_diagnose(_action))


@launch_app.command("instances", help="List current and historical launch instances.")
def instances(
    launch_id: str | None = typer.Argument(None),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        LaunchRegistryApplication(WorkspaceApplication().open(workspace)).instances(
            launch_id
        ),
        output,
    )


@launch_app.command("attach", help="Follow launch status and recent strategy output.")
def attach(
    launch_id: str,
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent strategy log lines to show."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    python: bool = typer.Option(
        False,
        "--python",
        help="Open a Python console routed to the running Strategy on this launch.",
    ),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    active = _running_instance(owner, launch_id)
    if active is None:
        raise typer.BadParameter(f"launch is not running: {launch_id}")
    instance = str(active["instance_id"])
    mode = str(active.get("mode") or "paper")
    target = _target(launch_id, instance, mode, workspace)
    log_path = owner.instance(mode, launch_id, instance).log("strategy.log")
    if python:
        if effective_output(output) is not OutputFormat.TEXT:
            raise typer.BadParameter("--python requires text output")
        _interactive_python(target.socket_path, log_path)
        return
    value = _decorate_launch_status(
        owner,
        launch_id,
        instance,
        mode,
        LaunchControlApplication(owner).status(target),
    )
    log_lines = (
        log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        if log_path.is_file() and lines
        else []
    )
    structured_logs = []
    for line in log_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {"message": line, "structured": False}
        structured_logs.append(record)
    value.update(
        {
            "socket": str(target.socket_path),
            "mode": mode,
            "launch_id": launch_id,
            "instance_id": instance,
            "stdout_log": str(log_path),
            "stdout": log_lines,
            "logs": structured_logs,
        }
    )
    _emit(value, output)


def _interactive_python(socket_path: Path, log_path: Path) -> None:
    """Run a split terminal frontend for Strategy ``on_command``."""
    import asyncio

    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import Frame, TextArea

    class PythonConsole:
        def __init__(self) -> None:
            self.request_number = 0
            self.log_offset = 0
            self.log_lines: list[str] = []
            self.status = "connecting"
            self.busy = False

    console = PythonConsole()
    output = TextArea(
        text="Waiting for strategy events...\n",
        read_only=True,
        scrollbar=True,
        wrap_lines=False,
        focusable=False,
        height=None,
    )
    source = TextArea(
        multiline=True,
        wrap_lines=False,
        scrollbar=True,
        prompt=lambda: "... " if "\n" in source.text else ">>> ",
        height=8,
    )
    footer = Window(
        content=FormattedTextControl(
            lambda: (
                f" {console.status}   Enter: run/next line   blank line: submit   "
                "Ctrl-D: exit"
            )
        ),
        height=1,
    )
    bindings = KeyBindings()
    input_focused = Condition(lambda: get_app().layout.has_focus(source))

    def append_output(text: str) -> None:
        if not text:
            return
        current = output.text
        output.text = (current + text)[-120_000:]
        output.buffer.cursor_position = len(output.text)

    def submit_source() -> None:
        text = source.text.strip("\n")
        if not text or console.busy:
            return
        source.buffer.set_document(Document("", 0))
        console.busy = True
        console.status = "running command"
        get_app().create_background_task(send_source(text))

    async def send_source(text: str) -> None:
        console.request_number += 1
        request_id = f"interactive:{console.request_number}"
        try:
            result = await UnixRestClient(socket_path).request(
                "POST",
                "/v1/command",
                json.dumps(
                    {
                        "request_id": request_id,
                        "kind": "interactive.python",
                        "source": text,
                    },
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
        except Exception as error:
            append_output(f"\n[{request_id}] error: {error}\n")
        else:
            if result.get("error"):
                append_output(
                    f"\n[{request_id}] {result.get('error_code') or 'error'}: "
                    f"{result['error']}\n"
                )
            else:
                append_output(f"\n[{request_id}] ok\n")
                if result.get("stdout"):
                    append_output(result["stdout"])
                if result.get("stderr"):
                    append_output(result["stderr"])
                value = result.get("result", {}).get("value")
                if value is not None:
                    append_output(repr(value) if not isinstance(value, str) else value)
                    append_output("\n")
        finally:
            console.busy = False
            console.status = "connected"
            get_app().invalidate()

    @bindings.add("enter", filter=input_focused)
    def _enter(event) -> None:
        buffer: Buffer = event.current_buffer
        line = buffer.document.current_line
        if not line.strip() and buffer.text.strip():
            submit_source()
            return
        try:
            complete = codeop.compile_command(buffer.text, symbol="exec")
        except (SyntaxError, OverflowError, ValueError) as error:
            # The remote evaluator supports top-level await, which codeop does
            # not recognize in all supported Python versions.
            if not line.lstrip().startswith("await "):
                append_output(f"\nsyntax error: {error}\n")
                buffer.reset()
                return
            complete = True
        if complete is not None and not line.rstrip().endswith(":"):
            submit_source()
        else:
            buffer.insert_text("\n")

    @bindings.add("c-d")
    def _exit(event) -> None:
        event.app.exit()

    app = Application(
        layout=Layout(
            HSplit(
                [
                    Frame(output, title="Strategy events"),
                    Frame(source, title="Python input"),
                    footer,
                ]
            ),
            focused_element=source,
        ),
        key_bindings=bindings,
        full_screen=True,
        mouse_support=False,
    )

    async def follow_logs() -> None:
        while True:
            try:
                if log_path.is_file():
                    with log_path.open(encoding="utf-8", errors="replace") as handle:
                        handle.seek(console.log_offset)
                        chunk = handle.read()
                        console.log_offset = handle.tell()
                    if chunk:
                        console.log_lines.extend(chunk.splitlines(keepends=True))
                        append_output("".join(console.log_lines[-2000:]))
                        console.log_lines.clear()
                    console.status = "connected"
            except OSError as error:
                console.status = f"log error: {error}"
            app.invalidate()
            await asyncio.sleep(0.5)

    async def run_console() -> None:
        app.create_background_task(follow_logs())
        await app.run_async()

    asyncio.run(run_console())


@launch_app.command("logs", help="Read or follow strategy logs for a launch.")
def logs(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent log lines to show."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Follow the selected log file."
    ),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    if follow and effective_output(output) is not OutputFormat.TEXT:
        raise typer.BadParameter("--follow currently supports text output only")
    root = owner.instance(mode, launch_id, instance).root / "logs"
    files = (
        sorted(path for path in root.rglob("*") if path.is_file())
        if root.is_dir()
        else []
    )
    payload = {
        "path": str(root),
        "exists": root.exists(),
        "files": [str(path) for path in files],
    }
    if files:
        strategy_log = root / "strategy.log"
        latest = strategy_log if strategy_log.is_file() else files[-1]
        payload["latest"] = str(latest)
        content = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        payload["lines"] = content[-lines:] if lines else []
    _emit(payload, output)
    if follow and files:
        position = latest.stat().st_size
        while True:
            try:
                with latest.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        typer.echo(line.rstrip("\n"), color=False)
                    position = stream.tell()
                time.sleep(0.25)
            except KeyboardInterrupt:
                return


@launch_app.command("artifacts", help="List files produced by a launch instance.")
def artifacts(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    root = owner.paths.launches / mode / launch_id / "instances" / resolved_instance
    _emit(
        {
            "path": str(root),
            "exists": root.exists(),
            "files": [str(path) for path in root.rglob("*")] if root.is_dir() else [],
        },
        output,
    )


@replay_app.command("events")
def replay_events(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(read_replay_events(file, limit=limit), output)


def _timeline_instance(owner, launch_id: str, instance_id: str):
    entries = [
        entry
        for entry in LaunchRegistryApplication(owner).instances(launch_id)
        if entry.get("instance_id") == instance_id
    ]
    if not entries:
        raise typer.BadParameter(
            f"launch instance is not registered: {launch_id}/{instance_id}"
        )
    if len(entries) > 1:
        raise typer.BadParameter(
            f"launch instance {launch_id}/{instance_id} exists in multiple modes"
        )
    mode = str(entries[0].get("mode") or "")
    if not mode:
        raise typer.BadParameter("registered launch instance has no mode")
    return owner.instance(mode, launch_id, instance_id)


@instance_timeline_app.command("list")
def launch_instance_timeline_list(
    launch_id: str = typer.Argument(..., help="Launch id."),
    instance_id: str = typer.Argument(..., help="Launch instance id."),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        instance = _timeline_instance(owner, launch_id, instance_id)
        records = LaunchInstanceTimelineApplication(instance).list(limit=limit)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(records, output)


@instance_timeline_app.command("export")
def launch_instance_timeline_export(
    launch_id: str = typer.Argument(..., help="Launch id."),
    instance_id: str = typer.Argument(..., help="Launch instance id."),
    destination: Path = typer.Option(
        ..., "--destination", "--output-file", help="Export file path."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        instance = _timeline_instance(owner, launch_id, instance_id)
        exported = LaunchInstanceTimelineApplication(instance).export(destination)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "launch_id": launch_id,
            "mode": instance.mode,
            "instance_id": instance_id,
            "destination": str(exported),
        },
        output,
    )
