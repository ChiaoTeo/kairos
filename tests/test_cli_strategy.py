from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from kairospy.application.strategy import CliStrategyBase, cli_command_envelope


def test_cli_strategy_base_records_trace_and_control_commands() -> None:
    strategy = CliStrategyBase()
    traces = []
    controls = []
    context = SimpleNamespace(
        strategy_id="cli-strategy",
        accounts=SimpleNamespace(),
        control=SimpleNamespace(request_pause=lambda **kwargs: controls.append(("pause", kwargs))),
        trace=lambda name, payload: traces.append((name, payload)),
    )

    strategy.on_system(
        context,
        cli_command_envelope("trace", {"name": "check", "payload": {"ok": True}}, time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    strategy.on_system(
        context,
        cli_command_envelope("control", {"kind": "pause", "reason": "test", "request_id": "pause-1"}, time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )

    assert traces == [("check", {"ok": True})]
    assert controls == [("pause", {"reason": "test", "request_id": "pause-1"})]
