from __future__ import annotations

import argparse
import json
import select
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from kairospy.infrastructure.storage.codec import to_primitive


def product_command(args: argparse.Namespace) -> int:
    from kairospy.integrations.connectors.binance.historical_archive import GracefulShutdown
    from kairospy.surface import product as product_surface
    from kairospy.surface.cli.output import render_error, render_product_result, resolve_language

    if args.group == "run":
        handler = _run_handler(args, product_surface)
        if handler is None:
            raise SystemExit(f"unsupported run action {args.action!r}")
        try:
            payload = handler(args)
            if args.action == "live" and args.live_action == "attach" and not getattr(args, "no_follow", False):
                return run_live_attach_console(args, product_surface, render_product_result, resolve_language)
        except GracefulShutdown as error:
            print(f"Stopped cleanly: {error}", file=sys.stderr)
            return 130
        except (KeyError, LookupError, PermissionError, ValueError, FileNotFoundError) as error:
            language = resolve_language(getattr(args, "lang", None))
            print(render_error(error, language, json_output=args.format == "json"), file=sys.stderr)
            return 2
        if args.format == "json":
            print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
        elif not args.quiet:
            print(render_product_result("run", args.action, payload, resolve_language(getattr(args, "lang", None))))
        return 0
    raise SystemExit(f"unsupported product group {args.group!r}")


def _run_handler(args: argparse.Namespace, product_surface):
    handlers = {
        "start": product_surface.run_start,
        "config": product_surface.run_config,
        "live": product_surface.run_live,
        "inspect": product_surface.run_inspect,
        "replay": product_surface.run_replay,
        "compare": product_surface.run_compare,
        "status": product_surface.run_status,
        "stop": product_surface.run_stop,
        "force-stop": product_surface.run_force_stop,
        "pause": product_surface.run_pause,
        "resume": product_surface.run_resume,
        "reduce-only": product_surface.run_reduce_only,
        "clear-reduce-only": product_surface.run_clear_reduce_only,
        "cancel-all": product_surface.run_cancel_all,
        "reconcile": product_surface.run_reconcile,
        "commands": product_surface.run_commands,
        "metrics": product_surface.run_metrics,
        "export": product_surface.run_export,
        "incidents": product_surface.run_incidents,
        "close-incident": product_surface.run_close_incident,
    }
    return handlers.get(args.action)


def workspace_command(args: argparse.Namespace) -> int:
    from kairospy.workspace import Workspace, WorkspaceRepository

    if args.action == "list":
        repository = WorkspaceRepository.discover(Path.cwd())
        workspaces = repository.list()
        payload = {
            "product": "workspace",
            "operation": "list",
            "workspace_count": len(workspaces),
            "workspaces": [item.manifest.to_dict() for item in workspaces],
        }
        _emit_workspace_payload(args, payload)
        return 0
    if args.action == "create":
        workspace = Workspace.open_or_create(args.name, start=Path.cwd())
        payload = {
            "product": "workspace",
            "operation": "create",
            "workspace": workspace.name,
            "root": str(workspace.root),
        }
        _emit_workspace_payload(args, payload)
        return 0
    if args.action == "attach":
        workspace = Workspace.open_or_create(args.workspace, start=Path.cwd())
        attachment = workspace.attach(
            args.name,
            dataset=args.dataset,
            view=args.view,
            instruments=tuple(args.instrument),
            fields=tuple(args.field),
            freshness_seconds=args.freshness_seconds,
        )
        payload = {
            "product": "workspace",
            "operation": "attach",
            "workspace": workspace.name,
            "attachment": attachment.to_dict(),
        }
        _emit_workspace_payload(args, payload)
        return 0
    if args.action == "inspect":
        workspace = Workspace.open_or_create(args.name, start=Path.cwd())
        payload = {
            "product": "workspace",
            "operation": "inspect",
            "workspace": workspace.name,
            "attachments": {
                name: attachment.to_dict()
                for name, attachment in workspace.attachments.bindings.items()
            },
        }
        _emit_workspace_payload(args, payload)
        return 0
    if args.action == "inspect-code":
        payload = workspace_inspect_code(
            args.entrypoint,
            tuple(args.param),
            mode=args.mode,
        )
        _emit_workspace_payload(args, payload)
        return 0
    raise SystemExit(f"unsupported workspace action: {args.action}")


def workspace_inspect_code(entrypoint_ref: str, params_values: tuple[str, ...], *, mode: str = "inspect") -> dict[str, object]:
    from kairospy.infrastructure.configuration import KairosProjectConfig
    from kairospy.surface import product as product_surface
    from kairospy.workspace import WorkspaceBuildContext

    params = {}
    for value in params_values:
        if "=" not in value:
            raise SystemExit(f"workspace parameter must be key=value: {value}")
        key, raw = value.split("=", 1)
        params[key] = raw
    config = KairosProjectConfig.discover(Path.cwd())
    _module, _callable_name, entrypoint = product_surface._load_run_entrypoint(entrypoint_ref, config.root)
    context = WorkspaceBuildContext(
        project_root=config.root,
        data_root=config.relative_path("paths.lake_root", ".kairos/data"),
    )
    projection = entrypoint(context, params)
    if projection is None:
        projection = context.project()
    if not hasattr(projection, "to_dict"):
        raise ValueError(f"workspace entrypoint must return WorkspaceProjection: {entrypoint_ref}")
    return {
        "product": "workspace",
        "operation": "inspect-code",
        "entrypoint": entrypoint_ref,
        "params": params,
        "projection": projection.to_dict(),
        "nodes": [node.to_dict() for node in projection.nodes],
        "preflight": projection.preflight(mode),
    }


def providers_command(args: argparse.Namespace) -> int:
    from kairospy.data.extensions.bootstrap import default_provider_registry, register_configured_products

    providers = default_provider_registry(args.lake_root)
    register_configured_products(providers, args.lake_root)
    payload = {
        "product": "providers",
        "operation": "list",
        "providers": [
            {"provider": provider, "products": sorted(products)}
            for provider, products in sorted(getattr(providers, "_provider_products", {}).items())
        ],
    }
    if args.format == "json":
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_live_attach_console(args: argparse.Namespace, product_surface: object, render_product_result: object, resolve_language: object) -> int:
    language = resolve_language(getattr(args, "lang", None))
    interval = float(getattr(args, "interval_seconds", 1.0))
    next_status_at = time.monotonic()
    last_key = None
    log_path = None
    log_offset = 0
    print(run_live_attach_prompt(args.run_id), end="", flush=True)
    while True:
        now = time.monotonic()
        if now >= next_status_at:
            status_args = _run_live_attach_start_args("status", args, [])
            payload = product_surface.run_live(status_args)
            log_path = run_live_attach_log_path(payload)
            key = run_live_attach_status_key(payload)
            if key != last_key:
                print()
                print(render_product_result("run", "live", payload, language))
                last_key = key
            if log_offset == 0:
                log_offset = run_live_attach_print_tail(log_path, int(getattr(args, "tail_lines", 80)))
            else:
                log_offset = run_live_attach_print_new_log(log_path, log_offset)
            next_status_at = now + interval
            print(run_live_attach_prompt(args.run_id), end="", flush=True)
        readable, _, _ = select.select([sys.stdin], [], [], 0.1)
        if not readable:
            continue
        line = sys.stdin.readline()
        if line == "":
            print()
            return 0
        parts = shlex.split(line)
        if not parts:
            print(run_live_attach_prompt(args.run_id), end="", flush=True)
            continue
        parts = run_live_attach_normalize_command_parts(parts)
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return 0
        command_args = _run_live_attach_start_args(command, args, parts[1:])
        payload = product_surface.run_live(command_args)
        print(render_product_result("run", "live", payload, language))


def run_live_attach_prompt(run_id: str) -> str:
    return f"kairos[{run_id}]> "


def run_live_attach_log_path(payload: dict[str, object]) -> Path | None:
    value = payload.get("log_file")
    return Path(value) if isinstance(value, str) and value else None


def run_live_attach_status_key(payload: dict[str, object]) -> tuple[object, ...]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    return (
        payload.get("status"),
        payload.get("phase"),
        payload.get("reason"),
        payload.get("stop_requested"),
        metrics.get("operator_command_backlog", 0),
        metrics.get("open_incident_count", 0),
    )


def run_live_attach_print_tail(path: Path | None, tail_lines: int) -> int:
    if path is None or not path.is_file():
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-tail_lines:]:
        print(line)
    return path.stat().st_size


def run_live_attach_print_new_log(path: Path | None, offset: int) -> int:
    if path is None or not path.is_file():
        return offset
    size = path.stat().st_size
    if size < offset:
        offset = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        chunk = handle.read()
        if chunk:
            print(chunk, end="" if chunk.endswith("\n") else "\n")
        return handle.tell()


def run_live_attach_normalize_command_parts(parts: list[str]) -> list[str]:
    if parts[:3] == ["kairospy", "run", "live"] or parts[:3] == ["kairos", "run", "live"]:
        return parts[3:]
    if parts[:2] == ["run", "live"]:
        return parts[2:]
    return parts


def run_live_attach_start_args(command: str, args: argparse.Namespace, parts: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=f"run live {command}", add_help=False)
    parser.add_argument("--run-id", default=args.run_id)
    parser.add_argument("--config", type=Path, default=getattr(args, "config", None))
    parser.add_argument("--param", action="append", default=list(getattr(args, "param", [])))
    parser.add_argument("--confirm-live", action="store_true", default=getattr(args, "confirm_live", False))
    parser.add_argument("--foreground", action="store_true", default=False)
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--poll-seconds", type=float, default=getattr(args, "poll_seconds", 0.25))
    parser.add_argument("--log-file", type=Path, default=getattr(args, "log_file", None))
    parser.add_argument("--stale-after-seconds", type=float, default=getattr(args, "stale_after_seconds", 5.0))
    parser.add_argument("--fresh", action="store_true", default=False)
    parser.add_argument("--wait", type=float, default=0.0)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--actor", default=getattr(args, "actor", "cli"))
    parser.add_argument("--risk-limits-hash", default=getattr(args, "risk_limits_hash", None))
    parser.add_argument("reason_words", nargs="*")
    parsed = parser.parse_args(parts)
    parsed.group = "run"
    parsed.action = "live"
    parsed.live_action = command
    if parsed.reason is None:
        parsed.reason = run_live_attach_reason(command, parsed.reason_words)
    return parsed


def run_live_attach_reason(command: str, parts: list[str]) -> str:
    if "--reason" in parts:
        index = parts.index("--reason")
        if index + 1 < len(parts):
            return parts[index + 1]
    if parts:
        return " ".join(parts)
    return f"operator requested {command}"


def _emit_workspace_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.format == "json":
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
