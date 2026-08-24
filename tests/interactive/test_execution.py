from __future__ import annotations

from pathlib import Path

from kairospy.surface.cli.interactive.execution import (
    execute_guided_command,
    with_workspace,
)
from kairospy.surface.cli.interactive.models import GuidedCommand


def test_workspace_is_added_once() -> None:
    workspace = Path("/tmp/demo")
    command = GuidedCommand(("project", "status"), "状态")
    assert with_workspace(command, workspace) == (
        "project", "status", "--workspace", "/tmp/demo"
    )
    existing = GuidedCommand(
        ("project", "status", "--workspace", "/tmp/other"), "状态"
    )
    assert with_workspace(existing, workspace) == existing.argv


def test_dangerous_command_can_be_cancelled(
    interactive_context, monkeypatch
) -> None:
    called = False

    def execute(_argv):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)
    execute_guided_command(
        interactive_context,
        GuidedCommand(("system", "repair"), "修复", dangerous=True),
        execute=execute,
        yes=False,
    )
    assert called is False
    assert interactive_context.last_status == 0


def test_product_command_can_hide_technical_argv(
    interactive_context, capsys
) -> None:
    execute_guided_command(
        interactive_context,
        GuidedCommand(
            ("market", "once", "--market-id", "internal-id"),
            "查看 AAPL 最新报价",
            show_command=False,
        ),
        execute=lambda _argv: 0,
        yes=False,
    )

    output = capsys.readouterr().out
    assert "查看 AAPL 最新报价" in output
    assert "准备执行" not in output
    assert "internal-id" not in output
