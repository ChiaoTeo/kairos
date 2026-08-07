from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from kairospy.application.strategy.services.composition import compose_strategy_process
from kairospy.application.workspace import WorkspaceApplication


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one KairosPy strategy server")
    parser.add_argument("command", nargs="?", choices=("server",), default="server")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--strategy", required=True, help="strategy module and factory, e.g. strategies.sma:strategy")
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--instance-id", "--instance", dest="instance_id", required=True)
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--strategy-root", type=Path)
    parser.add_argument("--params", default="{}", help="JSON object passed to the strategy factory")
    return parser


def _params(value: str) -> dict[str, object]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--params must be a JSON object")
    return parsed


async def _run(args: argparse.Namespace) -> None:
    workspace = WorkspaceApplication().open(args.workspace)
    composition = compose_strategy_process(
        workspace,
        strategy_ref=args.strategy,
        launch_id=args.launch_id,
        instance_id=args.instance_id,
        mode=args.mode,
        strategy_root=args.strategy_root,
        params=_params(args.params),
    )
    await composition.control.start()
    try:
        await composition.control.serve_until_stopped()
    finally:
        await composition.control.close()


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"strategy process failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
