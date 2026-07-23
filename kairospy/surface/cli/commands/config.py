from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT


def has_cli_option(raw_argv: list[str], name: str) -> bool:
    return any(item == name or item.startswith(name + "=") for item in raw_argv)


def apply_project_config_defaults(args: argparse.Namespace, raw_argv: list[str]) -> None:
    from kairospy.infrastructure.configuration import load_project_config_or_none

    config = load_project_config_or_none()
    if config is None:
        return
    setattr(args, "_kairospy_project_config", config)
    lake_root = config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)
    defaults = {
        "--lake-root": ("lake_root", "paths.lake_root", DEFAULT_LAKE_ROOT),
        "--dataset-root": ("dataset_root", "paths.dataset_root", str(lake_root / "curated")),
        "--catalog-path": ("catalog_path", "paths.catalog_path", str(lake_root / "catalog" / "instruments.json")),
        "--reference-catalog-path": ("reference_catalog_path", "paths.reference_catalog", str(lake_root / "reference" / "catalog.json")),
        "--event-log-path": ("event_log_path", "paths.event_log", str(lake_root / "events" / "kairospy.jsonl")),
    }
    for option, (attribute, dotted_path, default) in defaults.items():
        if not hasattr(args, attribute) or has_cli_option(raw_argv, option):
            continue
        value = config.relative_path(dotted_path, default)
        setattr(args, attribute, str(value))
    if hasattr(args, "data_root") and not has_cli_option(raw_argv, "--data-root"):
        setattr(args, "data_root", str(lake_root / "snapshots"))


def require_project_config(args: argparse.Namespace):
    from kairospy.infrastructure.configuration import KairosProjectConfig

    existing = getattr(args, "_kairospy_project_config", None)
    return existing if existing is not None else KairosProjectConfig.discover()


def config_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, set_config_value, unset_config_value
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    try:
        config = require_project_config(args)
        if args.action == "path":
            print(config.path)
            return 0
        if args.action == "show":
            payload = config.data if args.raw else config.to_redacted_dict()
            if args.format == "json":
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(render_key_value_panel("Kairos Configuration", flatten_config(payload)))
            return 0
        if args.action == "set":
            set_config_value(config.path, args.path, args.value)
            if not args.quiet:
                print(f"Set {args.path}")
            return 0
        if args.action == "unset":
            removed = unset_config_value(config.path, args.path)
            if not args.quiet:
                print(f"{'Removed' if removed else 'Not set'} {args.path}")
            return 0
        issues = config.validate()
        payload = {"path": str(config.path), "valid": not issues, "issues": issues}
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            rows = [{"name": "config", "status": "ok" if not issues else "warn", "detail": str(config.path)}]
            rows.extend({"name": "issue", "status": "warn", "detail": issue} for issue in issues)
            print(render_status_table("Kairos Config Validation", rows))
        return 2 if issues and args.strict else 0
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


def configure_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, set_config_value
    from kairospy.surface.cli.output import render_command_success

    try:
        config = require_project_config(args)
        if args.provider is None:
            args = prompt_configure_args(args)
        if args.provider == "massive":
            set_config_value(config.path, "credentials.massive_marketdata_primary.api_key", f"env:{args.api_key_env}")
            message = f"Configured Massive API key from {args.api_key_env}"
        elif args.provider == "binance":
            key_env = args.api_key_env or (
                "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"
                if args.environment == "testnet"
                else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"
            )
            secret_env = args.api_secret_env or (
                "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"
                if args.environment == "testnet"
                else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"
            )
            base = (
                "credentials.binance_trading_testnet_spot"
                if args.environment == "testnet"
                else "credentials.binance_trading_live_spot"
            )
            set_config_value(config.path, f"{base}.api_key", f"env:{key_env}")
            set_config_value(config.path, f"{base}.api_secret", f"env:{secret_env}")
            message = f"Configured Binance {args.environment} credentials from {key_env}/{secret_env}"
        else:
            raise SystemExit(f"unsupported provider: {args.provider}")
        if args.format == "json":
            print(json.dumps({"configured": args.provider, "path": str(config.path)}, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print(render_command_success("Kairos Provider Configured", (
                ("Provider", args.provider),
                ("Config", str(config.path)),
                ("Result", message),
            )))
        return 0
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


def doctor_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError

    checks: list[dict[str, object]] = []
    try:
        config = require_project_config(args)
    except ConfigError as exc:
        checks.append({"name": "project", "status": "error", "detail": str(exc)})
        print_doctor(checks, args.format)
        return 2
    checks.append({"name": "project", "status": "ok", "detail": str(config.path)})
    if not isinstance(config.get("project"), dict) or not config.get("project.name"):
        checks.append({"name": "config", "status": "warning", "detail": "[project].name is required"})
    else:
        checks.append({"name": "config", "status": "ok", "detail": "kairos.toml is structurally valid"})
    lake_root = config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)
    checks.append({
        "name": "data",
        "status": "ok" if lake_root.exists() else "warning",
        "detail": f"lake root: {lake_root}",
    })
    print_doctor(checks, args.format)
    failed = any(item["status"] == "error" or (args.strict and item["status"] == "warning") for item in checks)
    return 2 if failed else 0


def print_doctor(checks: list[dict[str, object]], output_format: str) -> None:
    from kairospy.surface.cli.output import render_next_steps, render_status_table

    next_steps = doctor_next_steps(checks)
    if output_format == "json":
        print(json.dumps({"checks": checks, "next_steps": next_steps}, ensure_ascii=False, indent=2))
        return
    print(render_status_table("Kairos Doctor", checks))
    if next_steps:
        print()
        print(render_next_steps(next_steps))


def doctor_next_steps(checks: list[dict[str, object]]) -> list[str]:
    steps: list[str] = []
    by_name = {str(item.get("name")): str(item.get("status", "")).lower() for item in checks}
    if by_name.get("project") == "error":
        steps.append("kairospy init")
        return steps
    if by_name.get("config") in {"warning", "warn", "error"}:
        steps.append("kairospy config validate")
    if by_name.get("data") in {"warning", "warn", "error"}:
        steps.append("kairospy data start")
    if not steps:
        steps.append("kairospy data catalog")
    return steps


def flatten_config(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(flatten_config(value, path))
        else:
            rows.append((path, value))
    return rows


def prompt_configure_args(args: argparse.Namespace) -> argparse.Namespace:
    from kairospy.surface.cli.prompts import prompt_choice, prompt_text

    if not args.interactive and not sys.stdin.isatty():
        raise SystemExit("configure requires a provider in non-interactive mode; use 'kairospy configure massive' or 'kairospy configure binance'")
    provider = prompt_choice("Provider", ("massive", "binance"), default="massive")
    setattr(args, "provider", provider)
    if provider == "massive":
        setattr(args, "api_key_env", prompt_text("Massive API key environment variable", "KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"))
        return args
    environment = prompt_choice("Binance environment", ("testnet", "live"), default="testnet")
    default_key = (
        "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"
        if environment == "testnet"
        else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"
    )
    default_secret = (
        "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"
        if environment == "testnet"
        else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"
    )
    setattr(args, "environment", environment)
    setattr(args, "api_key_env", prompt_text("Binance API key environment variable", default_key))
    setattr(args, "api_secret_env", prompt_text("Binance API secret environment variable", default_secret))
    return args
