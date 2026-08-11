from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any

from kairospy.application.strategy.services.composition import compose_strategy_process
from kairospy.application.observability import configure_from_environment, record_gauge
from kairospy.application.workspace import WorkspaceApplication
from kairospy.strategy import StrategyOutput


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one KairosPy strategy server")
    parser.add_argument("command", nargs="?", choices=("server",), default="server")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--strategy",
        required=True,
        help="strategy module and factory, e.g. strategies.sma:strategy",
    )
    parser.add_argument("--launch-id", required=True)
    parser.add_argument(
        "--instance-id", "--instance", dest="instance_id", required=True
    )
    parser.add_argument("--mode", default="paper")
    parser.add_argument(
        "--params", default="{}", help="JSON object passed to the strategy factory"
    )
    return parser


def _params(value: str) -> dict[str, object]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--params must be a JSON object")
    return parsed


def _write_backtest_report(composition, workspace) -> None:
    # The process mode is not part of StrategyHost's business state; this
    # helper is called only for a backtest process.
    instance = workspace.instance(
        "backtest", composition.host.launch_id, composition.host.instance_id
    )
    report = {
        "schema_version": 1,
        "launch_id": composition.host.launch_id,
        "instance_id": composition.host.instance_id,
        "strategy_id": composition.host.strategy.strategy_id,
        "completed_at_unix_nanos": time.time_ns(),
        "status": composition.host.status.state.value,
        "event_count": composition.host.status.event_count,
        "equity_curve": list(composition.host.equity_curve),
        "final_account": (
            composition.host.equity_curve[-1]["snapshot"]
            if composition.host.equity_curve
            else None
        ),
    }
    path = instance.state("backtest", "report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


async def _run(args: argparse.Namespace) -> None:
    workspace = WorkspaceApplication().open(args.workspace)
    telemetry = configure_from_environment(
        "strategy",
        instance_id=args.instance_id,
        workspace_id=workspace.identity.workspace_id,
    )
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    composition = None
    try:
        composition = compose_strategy_process(
            workspace,
            strategy_ref=args.strategy,
            launch_id=args.launch_id,
            instance_id=args.instance_id,
            mode=args.mode,
            params=_params(args.params),
        )
        sys.stdout = StrategyOutput(composition.host.logger, source="stdout")
        sys.stderr = StrategyOutput(composition.host.logger, source="stderr")
        await composition.control.start()
        record_gauge("kairos.process.ready", 1)
        await composition.control.serve_until_stopped()
        if args.mode == "backtest":
            _write_backtest_report(composition, workspace)
    finally:
        if composition is not None:
            await composition.control.close()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        if telemetry is not None:
            telemetry.force_flush()
            telemetry.shutdown()


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
