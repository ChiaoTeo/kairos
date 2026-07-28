from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Mapping, TextIO

import typer

from kairospy.config import load_run_config
from kairospy.modes.backtest import BacktestEngineDaemonTarget, backtest_result_summary
from kairospy.modes.paper import paper_result_summary
from kairospy.runtime import list_run_daemons
from kairospy.runtime.account_journal import RunAccountJournal
from kairospy.runtime.line import RuntimeMode
from kairospy.runtime.daemon import RunDaemonControlPlane, RunDaemonTarget
from kairospy.service.operations.run import RunConfigurationError, configured_event_mode, configured_streaming_paper_target
from kairospy.surface.runtime import DriverName, ExchangeName, exchange


run_app = typer.Typer(no_args_is_help=True, help="Run and daemon commands")
account_app = typer.Typer(no_args_is_help=True, help="Run account journal queries")
run_app.add_typer(account_app, name="account")


class OutputFormat(StrEnum):
    auto = "auto"
    json = "json"
    text = "text"


@run_app.command("config")
def config(
    action: str = typer.Argument(...),
    path: Path = typer.Argument(...),
) -> None:
    run_config = load_run_config(path)
    if action == "validate":
        report = run_config.validation_report()
        _echo({
            "path": str(report.path),
            "valid": report.valid,
            "issues": list(report.issues),
        })
        if not report.valid:
            raise typer.Exit(2)
        return
    if action == "explain":
        _echo(run_config.explain())
        return
    raise typer.BadParameter(f"unsupported config action: {action}")


@run_app.command("backtest")
def backtest(
    config_path: Path = typer.Option(..., "--config"),
    events: Path | None = typer.Option(None, "--events"),
) -> None:
    _echo(_run_configured_mode(RuntimeMode.BACKTEST, config_path, events=events))


@run_app.command("list")
def list_runs(
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    stale_after_seconds: float = typer.Option(5.0, "--stale-after-seconds"),
) -> None:
    statuses = list_run_daemons(mode=mode, stale_after_seconds=stale_after_seconds)
    rows = [_run_list_row(status.to_dict(), index=index) for index, status in enumerate(statuses, start=1)]
    if _use_json_output(output_format):
        for row in rows:
            _echo(row)
        return
    typer.echo(_render_run_list(rows))


@account_app.command("summary")
def account_summary(
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    control = RunDaemonControlPlane(run_id, mode=mode)
    payload = RunAccountJournal(control.directory).read_current()
    _echo_account_payload("summary", payload, output_format=output_format)


@account_app.command("positions")
def account_positions(
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    _echo_account_rows("positions", mode=mode, run_id=run_id, output_format=output_format, limit=limit)


@account_app.command("pnl")
def account_pnl(
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    limit: int | None = typer.Option(20, "--limit"),
) -> None:
    _echo_account_rows("pnl", mode=mode, run_id=run_id, output_format=output_format, limit=limit)


@account_app.command("fills")
def account_fills(
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    _echo_account_rows("fills", mode=mode, run_id=run_id, output_format=output_format, limit=limit)


@account_app.command("orders")
def account_orders(
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    _echo_account_rows("orders", mode=mode, run_id=run_id, output_format=output_format, limit=limit)


@account_app.command("trades")
def account_trades(
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    _echo_account_rows("trades", mode=mode, run_id=run_id, output_format=output_format, limit=limit)


@run_app.command("daemon")
def daemon(
    action: str = typer.Argument("status"),
    mode: RuntimeMode = typer.Option(..., "--mode"),
    run_id: str = typer.Option(..., "--run-id"),
    config_path: Path | None = typer.Option(None, "--config"),
    events: Path | None = typer.Option(None, "--events"),
    foreground: bool = typer.Option(False, "--foreground"),
    duration_seconds: float | None = typer.Option(None, "--duration-seconds"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds"),
    stale_after_seconds: float = typer.Option(5.0, "--stale-after-seconds"),
    log_file: Path | None = typer.Option(None, "--log-file"),
    reason: str | None = typer.Option(None, "--reason"),
    actor: str = typer.Option("cli", "--actor"),
    force: bool = typer.Option(False, "--force"),
    wait: float = typer.Option(0.0, "--wait"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
    tail_lines: int = typer.Option(20, "--tail-lines"),
) -> None:
    _daemon(
        mode,
        action=action,
        run_id=run_id,
        config_path=config_path,
        events=events,
        foreground=foreground,
        duration_seconds=duration_seconds,
        poll_seconds=poll_seconds,
        stale_after_seconds=stale_after_seconds,
        log_file=log_file,
        reason=reason,
        actor=actor,
        force=force,
        wait=wait,
        output_format=output_format,
        tail_lines=tail_lines,
    )


@run_app.command("shell")
def shell(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    session = RunShellSession()
    if command:
        for line in command:
            if session.handle(line):
                return
        return
    typer.echo(session.banner())
    typer.echo(session.menu())
    while True:
        try:
            line = input(session.prompt())
        except EOFError:
            typer.echo("")
            return
        except KeyboardInterrupt:
            typer.echo("\nUse `quit` to exit.")
            continue
        if session.handle(line):
            return


def _daemon(
    mode: RuntimeMode,
    *,
    action: str,
    run_id: str,
    config_path: Path | None,
    events: Path | None,
    foreground: bool,
    duration_seconds: float | None,
    poll_seconds: float,
    stale_after_seconds: float,
    log_file: Path | None,
    reason: str | None,
    actor: str,
    force: bool,
    wait: float,
    output_format: OutputFormat,
    tail_lines: int,
) -> None:
    control = RunDaemonControlPlane(run_id, mode=mode)
    if action == "start":
        _update_daemon_context(
            control,
            config_path=config_path,
            events=events,
            foreground=foreground,
            duration_seconds=duration_seconds,
            poll_seconds=poll_seconds,
            stale_after_seconds=stale_after_seconds,
            log_file=log_file,
        )
        if foreground or duration_seconds is not None:
            target = _configured_daemon_target(mode, config_path, events) if config_path is not None else None
            status = control.run_foreground(
                poll_seconds=poll_seconds,
                duration_seconds=duration_seconds,
                target=target,
            )
        else:
            status = control.start_background(
                poll_seconds=poll_seconds,
                stale_after_seconds=stale_after_seconds,
                log_file=log_file,
                extra_args=_daemon_target_args(config_path=config_path, events=events),
            )
        _echo_daemon_status(status.to_dict(), action=action, output_format=output_format)
        return
    if action == "status":
        _echo_daemon_status(
            control.status(stale_after_seconds=stale_after_seconds).to_dict(),
            action=action,
            output_format=output_format,
        )
        return
    if action in {"stop", "force-stop"}:
        command = control.request_stop(
            reason=reason or f"operator requested {action}",
            actor=actor,
            force=force or action == "force-stop",
        )
        if wait > 0:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                status = control.status(stale_after_seconds=stale_after_seconds)
                if status.phase.value == "stopped":
                    _echo_daemon_status(status.to_dict(), action=action, output_format=output_format)
                    return
                time.sleep(min(0.1, wait))
        _echo_daemon_command(
            command,
            control.status(stale_after_seconds=stale_after_seconds).to_dict(),
            output_format=output_format,
        )
        return
    if action == "attach":
        _attach(
            control,
            stale_after_seconds=stale_after_seconds,
            poll_seconds=poll_seconds,
            output_format=output_format,
            tail_lines=tail_lines,
        )
        return
    raise typer.BadParameter(f"unsupported daemon action: {action}")


def _configured_daemon_target(
    mode: RuntimeMode,
    config_path: Path | None,
    events: Path | None,
) -> RunDaemonTarget:
    if config_path is None:
        raise typer.BadParameter("--config is required for configured daemon runs")
    if mode is RuntimeMode.LIVE:
        raise typer.BadParameter("live daemon targets are configured by live runtime adapters")
    if mode is RuntimeMode.PAPER:
        return _configured_streaming_paper_target(config_path)
    try:
        configured = configured_event_mode(mode, config_path, events=events)
    except RunConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    return BacktestEngineDaemonTarget(configured.engine, configured.source, run_id=configured.run_id)


def _daemon_target_args(*, config_path: Path | None, events: Path | None) -> tuple[str, ...]:
    args: list[str] = []
    if config_path is not None:
        args.extend(("--config", str(config_path)))
    if events is not None:
        args.extend(("--events", str(events)))
    return tuple(args)


def _update_daemon_context(
    control: RunDaemonControlPlane,
    *,
    config_path: Path | None,
    events: Path | None,
    foreground: bool,
    duration_seconds: float | None,
    poll_seconds: float,
    stale_after_seconds: float,
    log_file: Path | None,
) -> None:
    values: dict[str, object] = {
        "command": "start",
        "launch": "foreground" if foreground or duration_seconds is not None else "background",
        "poll_seconds": poll_seconds,
        "stale_after_seconds": stale_after_seconds,
    }
    if config_path is not None:
        values["config_file"] = str(config_path)
        try:
            run_config = load_run_config(config_path)
        except Exception as error:
            values["config_error"] = f"{type(error).__name__}: {error}"
        else:
            values.update({
                "configured_run_id": run_config.run_id,
                "configured_mode": run_config.mode,
                "strategy": run_config.strategy or "",
                "root": str(run_config.root),
                "accounts": sorted(run_config.accounts),
            })
    if events is not None:
        values["events_file"] = str(events)
    if duration_seconds is not None:
        values["duration_seconds"] = duration_seconds
    if log_file is not None:
        values["log_file"] = str(log_file)
    control.update_context(values)


def _attach(
    control: RunDaemonControlPlane,
    *,
    stale_after_seconds: float,
    poll_seconds: float,
    output_format: OutputFormat,
    tail_lines: int,
) -> None:
    if not _interactive_attach(output_format):
        _watch_daemon_status(control, stale_after_seconds=stale_after_seconds, poll_seconds=poll_seconds, output_format=output_format)
        return
    session = RunAttachSession(
        control,
        stale_after_seconds=stale_after_seconds,
        poll_seconds=poll_seconds,
        output_format=output_format,
        tail_lines=tail_lines,
    )
    session.run()


def _watch_daemon_status(
    control: RunDaemonControlPlane,
    *,
    stale_after_seconds: float,
    poll_seconds: float,
    output_format: OutputFormat,
) -> None:
    last = None
    while True:
        status = control.status(stale_after_seconds=stale_after_seconds).to_dict()
        current = json.dumps(status, sort_keys=True)
        if current != last:
            _echo_daemon_status(status, action="attach", output_format=output_format)
            last = current
        if status["phase"] in {"stopped", "failed"}:
            return
        time.sleep(poll_seconds)


def _interactive_attach(output_format: OutputFormat) -> bool:
    if output_format is OutputFormat.json:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _echo(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _echo_account_payload(name: str, payload: dict[str, object], *, output_format: OutputFormat) -> None:
    if _use_json_output(output_format):
        _echo(payload)
        return
    typer.echo(_render_account_summary_or_empty(payload))


def _echo_account_rows(
    name: str,
    *,
    mode: RuntimeMode,
    run_id: str,
    output_format: OutputFormat,
    limit: int | None,
) -> None:
    control = RunDaemonControlPlane(run_id, mode=mode)
    rows = RunAccountJournal(control.directory).read_rows(name, limit=limit)
    if _use_json_output(output_format):
        for row in rows:
            _echo(row)
        return
    typer.echo(_render_account_rows(name, rows))


def _render_account_summary(payload: Mapping[str, object]) -> str:
    account_view = payload.get("account_view")
    view = account_view if isinstance(account_view, Mapping) else {}
    rows = [
        ("run", payload.get("run_id")),
        ("mode", payload.get("mode")),
        ("account", payload.get("account") or _nested(view, "context", "value")),
        ("equity", payload.get("final_equity") or payload.get("equity") or view.get("equity")),
        ("initial", payload.get("initial_equity") or view.get("initial_equity")),
        ("net profit", payload.get("net_profit") or view.get("net_profit")),
        ("return", payload.get("total_return") or view.get("total_return")),
        ("fills", payload.get("fills")),
        ("closed trades", payload.get("closed_trades")),
    ]
    lines = ["Account Summary"]
    lines.extend(f"  {label:<14} {value}" for label, value in rows if value not in {None, ""})
    if not any(value not in {None, ""} for _, value in rows):
        lines.append("  no account journal data recorded")
    return "\n".join(lines).rstrip()


def _render_account_summary_or_empty(payload: Mapping[str, object]) -> str:
    if not payload:
        return "Account Summary\n  no account journal data recorded"
    return _render_account_summary(payload)


def _run_list_row(payload: Mapping[str, object], *, index: int) -> dict[str, object]:
    context = payload.get("context")
    context = context if isinstance(context, Mapping) else {}
    result = payload.get("result")
    result = result if isinstance(result, Mapping) else {}
    return {
        "index": index,
        "mode": payload.get("mode"),
        "run_id": payload.get("run_id"),
        "status": payload.get("status"),
        "phase": payload.get("phase"),
        "updated_at": payload.get("updated_at"),
        "heartbeat_age_seconds": payload.get("heartbeat_age_seconds"),
        "strategy": context.get("strategy") or result.get("strategy_id") or result.get("latest_strategy_id") or "",
        "config": context.get("config_file") or "",
        "log_file": payload.get("log_file") or "",
    }


def _render_run_list(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "Runs\n  no run daemons recorded"
    return "Runs\n" + _render_table(
        ("index", "mode", "run_id", "status", "updated_at", "strategy"),
        rows,
    )


def _render_account_rows(name: str, rows: list[dict[str, object]]) -> str:
    title = f"Account {name.title()}"
    if not rows:
        return f"{title}\n  no account journal data recorded"
    columns = _account_columns(name, rows)
    table = _render_table(columns, rows)
    return f"{title}\n{table}".rstrip()


def _account_columns(name: str, rows: list[dict[str, object]]) -> tuple[str, ...]:
    preferred = {
        "positions": ("instrument_id", "quantity", "average_price", "mark_price", "unrealized_pnl", "time"),
        "pnl": ("time", "equity", "cash", "net_profit", "total_return"),
        "equity": ("time", "equity", "cash", "net_profit", "total_return"),
        "fills": ("occurred_at", "instrument_id", "side", "quantity", "price", "fee", "order_id", "intent_id"),
        "orders": ("client_order_id", "instrument_id", "status", "side", "quantity", "filled_quantity", "remaining_quantity"),
        "trades": ("opened_at", "closed_at", "instrument_id", "quantity", "entry_price", "exit_price", "net_pnl", "return_pct"),
    }.get(name, ())
    present = tuple(column for column in preferred if any(column in row for row in rows))
    extras = tuple(key for key in rows[0] if key not in present and key not in {"run_id", "mode"})
    return present or extras


def _render_table(columns: tuple[str, ...], rows: list[dict[str, object]]) -> str:
    widths = {
        column: max(
            len(column),
            *(len(_cell(row.get(column))) for row in rows),
        )
        for column in columns
    }
    header = "  " + "  ".join(f"{column:<{widths[column]}}" for column in columns)
    separator = "  " + "  ".join("-" * widths[column] for column in columns)
    body = [
        "  " + "  ".join(f"{_cell(row.get(column)):<{widths[column]}}" for column in columns)
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return _format_mapping(value)
    if isinstance(value, list):
        return _display_value(value)
    return str(value)


def _nested(value: Mapping[str, object], *keys: str) -> object | None:
    current: object = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _echo_daemon_status(payload: dict[str, object], *, action: str, output_format: OutputFormat) -> None:
    if _use_json_output(output_format):
        _echo(payload)
        return
    typer.echo(_render_daemon_status(payload, action=action))


def _echo_daemon_command(
    command: dict[str, object],
    status: dict[str, object],
    *,
    output_format: OutputFormat,
) -> None:
    if _use_json_output(output_format):
        _echo({"command": command, "status": status})
        return
    typer.echo(_render_daemon_command(command, status))


def _render_daemon_command(command: Mapping[str, object], status: Mapping[str, object]) -> str:
    lines = [_render_daemon_status(status, action="stop")]
    lines.extend((
        "",
        "Command",
        f"  requested: {command.get('requested_at', '')}",
        f"  actor: {command.get('actor', '')}",
        f"  reason: {command.get('reason', '')}",
    ))
    return "\n".join(lines).rstrip()


def _use_json_output(output_format: OutputFormat) -> bool:
    if output_format is OutputFormat.json:
        return True
    if output_format is OutputFormat.text:
        return False
    return not sys.stdout.isatty()


def _render_daemon_status(payload: Mapping[str, object], *, action: str) -> str:
    status = str(payload.get("status") or payload.get("phase") or "unknown")
    phase = str(payload.get("phase") or "unknown")
    run_id = str(payload.get("run_id") or "")
    mode = str(payload.get("mode") or "")
    lines = [f"{mode} run {run_id}: {_human_status(status)}", ""]
    rows: list[tuple[str, object]] = [
        ("phase", phase),
        ("reason", payload.get("reason") or ""),
        ("desired", payload.get("desired_state") or ""),
        ("heartbeat", _format_heartbeat(payload)),
        ("updated", payload.get("updated_at") or ""),
        ("log", payload.get("log_file") or ""),
    ]
    identity = payload.get("identity")
    if isinstance(identity, Mapping):
        rows.append(("process", _format_process(identity)))
    context = payload.get("context")
    if isinstance(context, Mapping) and context:
        rows.append(("context", _format_context(context)))
    metrics = payload.get("metrics")
    if isinstance(metrics, Mapping) and metrics:
        rows.append(("metrics", _format_mapping(metrics)))
    result = payload.get("result")
    if isinstance(result, Mapping) and result:
        rows.append(("result", _format_mapping(result)))
    lines.extend(f"  {label:<10} {value}" for label, value in rows if value not in {None, ""})
    next_steps = _daemon_next_steps(payload, action=action)
    if next_steps:
        lines.extend(("", "Next", *(f"  {step}" for step in next_steps)))
    return "\n".join(lines).rstrip()


def _human_status(status: str) -> str:
    if status == "stale":
        return "stale heartbeat"
    return status


def _format_heartbeat(payload: Mapping[str, object]) -> str:
    heartbeat = payload.get("heartbeat_at")
    age = payload.get("heartbeat_age_seconds")
    if heartbeat is None:
        return "none"
    if isinstance(age, (int, float)):
        return f"{heartbeat} ({age:.1f}s ago)"
    return str(heartbeat)


def _format_process(identity: Mapping[str, object]) -> str:
    parts = []
    if identity.get("pid") is not None:
        parts.append(f"pid={identity['pid']}")
    if identity.get("host"):
        parts.append(f"host={identity['host']}")
    if identity.get("started_at"):
        parts.append(f"started={identity['started_at']}")
    return ", ".join(parts)


def _format_context(context: Mapping[str, object]) -> str:
    keys = ("config_file", "strategy", "configured_run_id", "launch", "events_file")
    selected = {key: context[key] for key in keys if key in context and context[key] not in {None, ""}}
    return _format_mapping(selected or context)


def _format_mapping(values: Mapping[str, object]) -> str:
    return ", ".join(f"{key}={_display_value(value)}" for key, value in values.items())


def _display_value(value: object) -> str:
    if isinstance(value, Mapping):
        return "{" + _format_mapping(value) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_display_value(item) for item in value) + "]"
    return str(value)


def _daemon_next_steps(payload: Mapping[str, object], *, action: str) -> list[str]:
    run_id = payload.get("run_id")
    mode = payload.get("mode")
    if not run_id or not mode:
        return []
    status = str(payload.get("status") or payload.get("phase") or "")
    base = "kairospy run daemon"
    if status in {"running", "starting"}:
        return [
            f"{base} attach --mode {mode} --run-id {run_id}",
            f"{base} stop --mode {mode} --run-id {run_id} --wait 5",
            f"{base} status --mode {mode} --run-id {run_id} --format json",
        ]
    if action == "start":
        return [f"{base} status --mode {mode} --run-id {run_id}"]
    return []


class RunAttachSession:
    def __init__(
        self,
        control: RunDaemonControlPlane,
        *,
        stale_after_seconds: float,
        poll_seconds: float,
        output_format: OutputFormat,
        tail_lines: int,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.control = control
        self.stale_after_seconds = stale_after_seconds
        self.poll_seconds = poll_seconds
        self.output_format = output_format
        self.tail_lines = tail_lines
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._log_offset = 0

    def run(self) -> None:
        self._write(self.banner())
        self.print_status()
        self.print_tail(self.tail_lines)
        while True:
            try:
                line = input(self.prompt())
            except EOFError:
                self._write("")
                return
            except KeyboardInterrupt:
                self._write("\nUse `quit` to exit attach.")
                continue
            if self.handle(line):
                return

    def banner(self) -> str:
        return (
            f"Attached to {self.control.mode.value} run {self.control.run_id}. "
            "Commands: status, summary, positions, pnl, fills, orders, tail [n], stop [reason], json, context, refresh, quit."
        )

    def prompt(self) -> str:
        return f"kairospy/{self.control.mode.value}[{self.control.run_id}]> "

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        if not parts:
            self.refresh()
            return False
        command = parts[0]
        if command in {"quit", "exit", "q", "detach"}:
            return True
        if command in {"help", "?", "menu"}:
            self._write(self.banner())
            return False
        if command in {"status", "s"}:
            self.print_status()
            return False
        if command in {"refresh", "r"}:
            self.refresh()
            return False
        if command == "json":
            _echo(self.status())
            return False
        if command == "context":
            self.print_context()
            return False
        if command == "tail":
            self.print_tail(_optional_int(parts[1:], self.tail_lines))
            return False
        if command in {"summary", "positions", "pnl", "fills", "orders", "trades"}:
            self.print_account(command, parts[1:])
            return False
        if command == "stop":
            reason = " ".join(parts[1:]) or "operator requested stop"
            command_payload = self.control.request_stop(reason=reason, actor="attach")
            if _use_json_output(self.output_format):
                _echo({"command": command_payload, "status": self.status()})
            else:
                self._write(_render_daemon_command(command_payload, self.status()))
            return False
        if command == "force-stop":
            reason = " ".join(parts[1:]) or "operator requested force-stop"
            command_payload = self.control.request_stop(reason=reason, actor="attach", force=True)
            if _use_json_output(self.output_format):
                _echo({"command": command_payload, "status": self.status()})
            else:
                self._write(_render_daemon_command(command_payload, self.status()))
            return False
        self._write(f"Unknown attach command: {command}")
        return False

    def refresh(self) -> None:
        self.print_status()
        self.print_new_log()

    def status(self) -> dict[str, object]:
        return self.control.status(stale_after_seconds=self.stale_after_seconds).to_dict()

    def print_status(self) -> None:
        self._write(_render_daemon_status(self.status(), action="attach"))

    def print_context(self) -> None:
        context = self.status().get("context")
        if isinstance(context, Mapping) and context:
            self._write(_render_key_values("Context", context))
        else:
            self._write("Context\n  none")

    def print_account(self, command: str, parts: list[str]) -> None:
        limit = _option_value_int(parts, "--limit")
        journal = RunAccountJournal(self.control.directory)
        if command == "summary":
            self._write(_render_account_summary_or_empty(journal.read_current()))
            return
        self._write(_render_account_rows(command, journal.read_rows(command, limit=limit)))

    def print_tail(self, lines: int) -> None:
        text = _tail_file(self.control.log_path, lines)
        self._log_offset = _file_size(self.control.log_path)
        self._write("Log")
        self._write(text or "  no log output")

    def print_new_log(self) -> None:
        text, offset = _read_file_from(self.control.log_path, self._log_offset)
        self._log_offset = offset
        if text:
            self._write("Log")
            self._write(text.rstrip())

    def _write(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()


class RunShellSession:
    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
    ) -> None:
        self.stdout = stdout or sys.stdout
        self.mode = RuntimeMode.PAPER
        self.run_id: str | None = None
        self.run_choices: list[dict[str, object]] = []

    def banner(self) -> str:
        return (
            "Kairos run workspace. Use `list` to refresh runs, `use <#>` to select one, "
            "then inspect or control it."
        )

    def menu(self) -> str:
        selected = (
            f"{self.mode.value}:{self.run_id}"
            if self.run_id is not None
            else "none"
        )
        return "\n".join([
            "Run Workspace",
            f"  selected  {selected}",
            "",
            "Commands",
            "  list [--mode paper|backtest|live]   refresh run list",
            "  use <#>                              select a numbered run",
            "  use <mode> <run-id>                  select a run by id",
            "  status                               show selected run status",
            "  attach                               open selected run monitor",
            "  stop [reason]                        request selected run stop",
            "  start --config <path>                start selected run from config",
            "  summary | positions | pnl            account views",
            "  fills | orders | trades              execution views",
            "  back                                 clear selected run",
            "  quit                                 exit shell",
        ])

    def prompt(self) -> str:
        if self.run_id:
            return f"kairospy/run[{self.mode.value}:{self.run_id}]> "
        return "kairospy/run> "

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        if not parts:
            return False
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return True
        if command in {"help", "?", "menu"}:
            self._write(self.menu())
            return False
        if command in {"back", "clear"}:
            self.run_id = None
            self._write(self.menu())
            return False
        if command.isdigit() and self.run_choices:
            return self._handle_choice(command)
        if command == "use":
            return self._handle_use(parts[1:])
        if command in {"list", "ls", "runs"}:
            self._list_runs(parts[1:])
            return False
        if command in {"status", "s", "attach", "stop", "force-stop", "start"}:
            self._run_daemon_command(command, parts[1:])
            return False
        if command in {"positions", "pnl", "fills", "orders", "trades", "summary"}:
            self._run_account_command(command, parts[1:])
            return False
        self._write(f"Unknown run shell command: {command}")
        return False

    def _handle_use(self, parts: list[str]) -> bool:
        if len(parts) == 1 and parts[0].isdigit():
            return self._handle_choice(parts[0])
        if len(parts) < 2:
            self._write("Usage: use <paper|backtest|live> <run-id>")
            return False
        self.mode = RuntimeMode(parts[0])
        self.run_id = parts[1]
        self._write(f"Using {self.mode.value} run {self.run_id}")
        return False

    def _handle_choice(self, value: str) -> bool:
        index = int(value)
        if index < 1 or index > len(self.run_choices):
            self._write(f"Unknown run choice: {value}")
            return False
        row = self.run_choices[index - 1]
        self.mode = RuntimeMode(str(row["mode"]))
        self.run_id = str(row["run_id"])
        self._write(f"Using {self.mode.value} run {self.run_id}")
        return False

    def _list_runs(self, parts: list[str]) -> None:
        mode = None
        if "--mode" in parts:
            index = parts.index("--mode")
            if index + 1 < len(parts):
                mode = RuntimeMode(parts[index + 1])
        statuses = list_run_daemons(mode=mode)
        self.run_choices = [
            _run_list_row(status.to_dict(), index=index)
            for index, status in enumerate(statuses, start=1)
        ]
        self._write(_render_run_list(self.run_choices))

    def _run_daemon_command(self, command: str, parts: list[str]) -> None:
        if self.run_id is None:
            self._write("Select a run first: use <paper|backtest|live> <run-id>")
            return
        action = "status" if command in {"status", "s"} else command
        argv = [
            "daemon",
            action,
            "--mode",
            self.mode.value,
            "--run-id",
            self.run_id,
            "--format",
            "text",
            *parts,
        ]
        try:
            _daemon_command_from_shell(argv)
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
            if code:
                self._write(f"Command exited with status {code}")
        except (KeyError, LookupError, PermissionError, ValueError, FileNotFoundError) as error:
                self._write(f"Command failed: {error}")

    def _run_account_command(self, command: str, parts: list[str]) -> None:
        if self.run_id is None:
            self._write("Select a run first: use <paper|backtest|live> <run-id>")
            return
        action = "summary" if command == "summary" else command
        argv = [
            "account",
            action,
            "--mode",
            self.mode.value,
            "--run-id",
            self.run_id,
            "--format",
            "text",
            *parts,
        ]
        try:
            _run_command_from_shell(argv)
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
            if code:
                self._write(f"Command exited with status {code}")
        except (KeyError, LookupError, PermissionError, ValueError, FileNotFoundError) as error:
            self._write(f"Command failed: {error}")

    def _write(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()


def _daemon_command_from_shell(argv: list[str]) -> None:
    _run_command_from_shell(argv)


def _run_command_from_shell(argv: list[str]) -> None:
    from typer.main import get_command

    command = get_command(run_app)
    result = command.main(args=argv, standalone_mode=False)
    if isinstance(result, int) and result:
        raise SystemExit(result)


def _render_key_values(title: str, values: Mapping[str, object]) -> str:
    lines = [title]
    lines.extend(f"  {key:<16} {_display_value(value)}" for key, value in values.items())
    return "\n".join(lines).rstrip()


def _tail_file(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    selected = text.splitlines()[-max(lines, 0):]
    return "\n".join(selected)


def _read_file_from(path: Path, offset: int) -> tuple[str, int]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            text = handle.read()
            return text, handle.tell()
    except FileNotFoundError:
        return "", 0


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _optional_int(parts: list[str], default: int) -> int:
    if not parts:
        return default
    try:
        return int(parts[0])
    except ValueError:
        return default


def _option_value_int(parts: list[str], name: str) -> int | None:
    if name not in parts:
        return None
    index = parts.index(name)
    if index + 1 >= len(parts):
        return None
    try:
        return int(parts[index + 1])
    except ValueError:
        return None


def _run_configured_mode(mode: RuntimeMode, config_path: Path, *, events: Path | None = None) -> dict[str, object]:
    try:
        configured = configured_event_mode(mode, config_path, events=events)
    except RunConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    result = configured.engine.run(configured.source)
    summary = paper_result_summary(result) if mode is RuntimeMode.PAPER else backtest_result_summary(result)
    return _jsonable({"run_id": configured.run_id, **summary})


def _configured_streaming_paper_target(config_path: Path) -> RunDaemonTarget:
    try:
        return configured_streaming_paper_target(
            config_path,
            exchange_factory=lambda venue: exchange(_exchange_name(venue), DriverName.ccxt),
        )
    except RunConfigurationError as error:
        raise typer.BadParameter(str(error)) from error


def _exchange_name(venue: str) -> ExchangeName:
    try:
        return ExchangeName(venue)
    except ValueError as error:
        raise typer.BadParameter(f"unsupported paper market data venue: {venue}") from error


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["run_app"]
