from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from kairospy.application.strategy.services.composition import compose_strategy_process
from kairospy.application.workspace import WorkspaceApplication
from kairospy.strategy import StrategyOutput


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one KairosPy strategy server")
    parser.add_argument("command", nargs="?", choices=("server",), default="server")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--strategy", required=True, help="strategy module and factory, e.g. strategies.sma:strategy")
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--instance-id", "--instance", dest="instance_id", required=True)
    parser.add_argument("--mode", default="paper")
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
        params=_params(args.params),
    )
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = StrategyOutput(composition.host.logger, source="stdout")
    sys.stderr = StrategyOutput(composition.host.logger, source="stderr")
    try:
        await composition.control.start()
        await composition.control.serve_until_stopped()
    finally:
        await composition.control.close()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr


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
