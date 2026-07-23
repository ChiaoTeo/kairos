from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kairospy.infrastructure.storage.codec import to_primitive


def project_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, DEFAULT_LAKE_ROOT
    from kairospy.surface.cli.commands.config import require_project_config
    from kairospy.surface.cli.output import render_key_value_panel

    try:
        config = require_project_config(args)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    if args.action != "status":
        raise SystemExit(f"unsupported project action: {args.action}")
    payload = {
        "project": config.get("project.name", config.root.name),
        "root": str(config.root),
        "config": str(config.path),
        "data_root": str(config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)),
        "default_environment": config.get("execution.default_environment", "simulated"),
        "live_trading": "enabled" if config.get("execution.live_trading_enabled", False) else "locked",
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_key_value_panel(
            "Kairos Project Status",
            tuple((key.replace("_", " ").title(), value) for key, value in payload.items()),
        ))
    return 0


def providers_command(args: argparse.Namespace) -> int:
    from kairospy.integrations.data_products.bootstrap import default_provider_registry, register_configured_products

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
