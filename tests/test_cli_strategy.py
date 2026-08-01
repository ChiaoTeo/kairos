from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from kairospy.application.strategy import CliStrategyBase, cli_command_envelope


def test_cli_strategy_base_records_trace_commands() -> None:
    strategy = CliStrategyBase()
    traces = []
    context = SimpleNamespace(
        strategy_id="cli-strategy",
        accounts=SimpleNamespace(),
        trace=lambda name, payload: traces.append((name, payload)),
    )

    strategy.on_system(
        context,
        cli_command_envelope("trace", {"name": "check", "payload": {"ok": True}}, time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )

    assert traces == [("check", {"ok": True})]


def test_cli_strategy_base_rejects_removed_control_command() -> None:
    strategy = CliStrategyBase()
    context = SimpleNamespace(strategy_id="cli-strategy")

    with pytest.raises(ValueError, match="unsupported cli strategy command: control"):
        strategy.on_system(
            context,
            cli_command_envelope("control", {"kind": "pause", "reason": "test", "request_id": "pause-1"}, time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )


def test_cli_strategy_base_rejects_status_command() -> None:
    strategy = CliStrategyBase()
    context = SimpleNamespace(strategy_id="cli-strategy")

    with pytest.raises(ValueError, match="unsupported cli strategy command: status"):
        strategy.on_system(
            context,
            cli_command_envelope("status", {}, time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )
