from __future__ import annotations

from contextlib import nullcontext
import json
import shlex
import sys
import threading
from typing import Mapping

import typer

from kairospy.application.support.system.application.control.attach import LaunchAttachSession, heartbeat_age_seconds
from kairospy.application.support.system.application.control import RuntimeMode
from kairospy.surface.interactive.line_reader import default_line_reader


class RuntimeAttachShell:
    def __init__(self, session: LaunchAttachSession, *, interval_seconds: float = 0.5) -> None:
        self.session = session
        self.interval_seconds = interval_seconds

    def run(self) -> None:
        log_file = self.session.target.log_file
        if log_file is None:
            raise ValueError("launch log was not found")
        if not sys.stdin.isatty():
            _tail_file(self.session)
            return
        reader = default_line_reader()
        output_lock = threading.Lock()
        position = 0
        with _patch_stdout_context():
            _echo_locked(output_lock, "[runtime/attach] Attached to runtime. Type `help` for commands, `exit` to detach.")
            chunk = self.session.read_log_since(position)
            position = chunk.position
            if chunk.text:
                _echo_log_chunk(output_lock, chunk.text)
            stop = threading.Event()
            follower = threading.Thread(
                target=_follow_logs,
                args=(self.session, position, stop, output_lock),
                kwargs={"interval_seconds": self.interval_seconds},
                daemon=True,
            )
            heartbeat = threading.Thread(
                target=_follow_state_heartbeat,
                args=(self.session, stop, output_lock),
                daemon=True,
            )
            follower.start()
            heartbeat.start()
            try:
                while True:
                    try:
                        line = reader.read(_prompt(self.session))
                    except EOFError:
                        _echo_locked(output_lock, "")
                        return
                    except KeyboardInterrupt:
                        _echo_locked(output_lock, "\n[runtime/attach] Use `exit` to detach.")
                        continue
                    command = line.strip()
                    if not command:
                        continue
                    if command in {"exit", "quit", "q", "detach"}:
                        return
                    if command in {"help", "?", "menu"}:
                        _echo_prefixed_block(output_lock, "[runtime/help]", attach_help_text(self.session))
                        continue
                    try:
                        label, result = handle_attach_shell_command(self.session, command)
                    except ValueError as error:
                        _echo_locked(output_lock, f"[runtime/error] {error}")
                        continue
                    _echo_prefixed_block(output_lock, f"[runtime/{label}]", json.dumps(result, indent=2, sort_keys=True, default=str))
            finally:
                stop.set()
                follower.join(timeout=1.0)
                heartbeat.join(timeout=1.0)


def handle_attach_shell_command(session: LaunchAttachSession, line: str) -> tuple[str, Mapping[str, object]]:
    request = parse_attach_shell_command(session, line)
    kind = str(request["kind"])
    payload = request["payload"] if isinstance(request["payload"], Mapping) else {}
    if kind == "attach.status":
        return "status", session.status()
    if kind == "attach.inspect":
        return "inspect", session.inspect()
    if kind == "runtime.stop":
        return "response", session.stop()
    return "response", session.submit_command(
        kind,
        payload,
        wait=bool(request["wait"]),
        timeout_seconds=float(request["timeout_seconds"]),
    )


def parse_attach_shell_command(session: LaunchAttachSession, line: str) -> dict[str, object]:
    parts = shlex.split(line.strip())
    if not parts:
        raise ValueError("empty attach command")
    wait = True
    timeout_seconds = 5.0
    payload: Mapping[str, object] | None = None
    command = parts[0]
    args = parts[1:]
    if command == "status":
        _reject_args(args)
        return {"kind": "attach.status", "payload": {}, "wait": wait, "timeout_seconds": timeout_seconds}
    if command == "inspect":
        _reject_args(args)
        return {"kind": "attach.inspect", "payload": {}, "wait": wait, "timeout_seconds": timeout_seconds}
    if command == "stop":
        _reject_args(args)
        return {"kind": "runtime.stop", "payload": {"reason": "requested from attach"}, "wait": wait, "timeout_seconds": timeout_seconds}
    if command in {"trade-status", "trade-acquire", "trade-release"}:
        _require_system(session)
        return _parse_trade_command(command, args, wait=wait, timeout_seconds=timeout_seconds)
    if command != "command":
        raise ValueError(f"unsupported attach command: {command}; use `help` for commands")
    kind, args = _pop_required_positional(args, "command requires kind")
    index = 0
    positional_payloads: list[str] = []
    while index < len(args):
        arg = args[index]
        if arg == "--no-wait":
            wait = False
        elif arg == "--wait":
            wait = True
        elif arg == "--timeout":
            index += 1
            if index >= len(args):
                raise ValueError("--timeout requires seconds")
            timeout_seconds = float(args[index])
        elif arg == "--payload-json":
            index += 1
            if index >= len(args):
                raise ValueError("--payload-json requires JSON object")
            payload = _json_object(args[index])
        elif arg.startswith("--"):
            raise ValueError(f"unsupported attach option: {arg}")
        else:
            positional_payloads.append(arg)
        index += 1
    if positional_payloads:
        if len(positional_payloads) > 1:
            raise ValueError("attach command accepts at most one JSON payload argument")
        payload = _json_object(positional_payloads[0])
    return {"kind": kind, "payload": dict(payload or {}), "wait": wait, "timeout_seconds": timeout_seconds}


def attach_help_text(session: LaunchAttachSession) -> str:
    lines = [
        "command KIND [JSON|--payload-json JSON] [--wait|--no-wait] [--timeout SECONDS]",
        "status",
        "inspect",
        "stop",
        "exit",
    ]
    if session.target.mode is RuntimeMode.SYSTEM:
        lines[1:1] = ["trade-status [ACCOUNT]", "trade-acquire ACCOUNT", "trade-release ACCOUNT"]
    return "\n".join(lines)


def _tail_file(session: LaunchAttachSession, *, interval_seconds: float = 0.5) -> None:
    position = 0
    try:
        while True:
            chunk = session.read_log_since(position)
            position = chunk.position
            if chunk.text:
                typer.echo(chunk.text, nl=False)
            threading.Event().wait(interval_seconds)
    except KeyboardInterrupt:
        return


def _follow_logs(
    session: LaunchAttachSession,
    position: int,
    stop: threading.Event,
    output_lock: threading.Lock,
    *,
    interval_seconds: float,
) -> None:
    while not stop.wait(interval_seconds):
        chunk = session.read_log_since(position)
        position = chunk.position
        if chunk.text:
            _echo_log_chunk(output_lock, chunk.text)


def _follow_state_heartbeat(session: LaunchAttachSession, stop: threading.Event, output_lock: threading.Lock, *, interval_seconds: float = 1.0) -> None:
    last_heartbeat_at: str | None = None
    while not stop.wait(interval_seconds):
        state = session.read_state()
        heartbeat_at = state.get("heartbeat_at")
        if not isinstance(heartbeat_at, str) or not heartbeat_at:
            continue
        if heartbeat_at == last_heartbeat_at:
            continue
        last_heartbeat_at = heartbeat_at
        phase = str(state.get("phase") or state.get("status") or "unknown")
        age = heartbeat_age_seconds(heartbeat_at)
        age_text = "" if age is None else f" age={age:.1f}s"
        _echo_locked(output_lock, f"[runtime/heartbeat] phase={phase} heartbeat_at={heartbeat_at}{age_text}")


def _parse_trade_command(command: str, args: list[str], *, wait: bool, timeout_seconds: float) -> dict[str, object]:
    if command == "trade-status":
        account, args = _pop_optional_positional(args)
        _reject_args(args)
        return {"kind": "account.trade-status", "payload": {"account": account} if account else {}, "wait": wait, "timeout_seconds": timeout_seconds}
    if command == "trade-acquire":
        account, args = _pop_required_positional(args, "trade-acquire requires account")
        _reject_args(args)
        return {"kind": "account.trade-acquire", "payload": {"account": account}, "wait": wait, "timeout_seconds": timeout_seconds}
    account, args = _pop_required_positional(args, "trade-release requires account")
    _reject_args(args)
    return {"kind": "account.trade-release", "payload": {"account": account}, "wait": wait, "timeout_seconds": timeout_seconds}


def _echo_locked(lock: threading.Lock, message: str, *, nl: bool = True) -> None:
    with lock:
        typer.echo(message, nl=nl)


def _echo_log_chunk(lock: threading.Lock, chunk: str) -> None:
    _echo_prefixed_block(lock, "[runtime/log]", chunk, preserve_end=True)


def _echo_prefixed_block(lock: threading.Lock, prefix: str, text: str, *, preserve_end: bool = False) -> None:
    lines = text.splitlines(keepends=preserve_end)
    if not lines:
        _echo_locked(lock, prefix)
        return
    with lock:
        for line in lines:
            if preserve_end:
                typer.echo(f"{prefix} {line}", nl=not line.endswith("\n"))
            else:
                typer.echo(f"{prefix} {line}")


def _patch_stdout_context():
    if not sys.stdout.isatty():
        return nullcontext()
    try:
        from prompt_toolkit.patch_stdout import patch_stdout
    except Exception:
        return nullcontext()
    return patch_stdout()


def _prompt(session: LaunchAttachSession) -> str:
    return f"kairos/{session.target.mode.value}> "


def _json_object(value: str) -> Mapping[str, object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"expected JSON object: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("expected JSON object")
    return payload


def _pop_optional_positional(args: list[str]) -> tuple[str | None, list[str]]:
    if not args or args[0].startswith("--"):
        return None, args
    return args[0], args[1:]


def _pop_required_positional(args: list[str], message: str) -> tuple[str, list[str]]:
    value, rest = _pop_optional_positional(args)
    if value is None:
        raise ValueError(message)
    return value, rest


def _reject_args(args: list[str]) -> None:
    if args:
        raise ValueError(f"unexpected arguments: {' '.join(args)}")


def _require_system(session: LaunchAttachSession) -> None:
    if session.target.mode is not RuntimeMode.SYSTEM:
        raise ValueError("trade commands are only available for system attach")


__all__ = ["RuntimeAttachShell", "attach_help_text", "handle_attach_shell_command", "parse_attach_shell_command"]
