from __future__ import annotations

from pathlib import Path

import click

from kairospy.surface.cli.interactive.execution import (
    execute_guided_command,
    with_workspace,
)
from kairospy.surface.cli.interactive.models import CommandExecution, GuidedCommand


def test_workspace_is_added_once() -> None:
    workspace = Path("/tmp/demo")
    command = GuidedCommand(("project", "status"), "状态")
    assert with_workspace(command, workspace) == (
        "project",
        "status",
        "--workspace",
        "/tmp/demo",
    )
    existing = GuidedCommand(("project", "status", "--workspace", "/tmp/other"), "状态")
    assert with_workspace(existing, workspace) == existing.argv


def test_dangerous_command_can_be_cancelled(interactive_context, monkeypatch) -> None:
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


def test_dangerous_command_explains_its_specific_effect(
    interactive_context, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)

    execute_guided_command(
        interactive_context,
        GuidedCommand(
            ("notifications", "test", "ops"),
            "测试通知",
            dangerous=True,
            confirmation="将向真实外部渠道发送一条测试消息。",
        ),
        execute=lambda _argv: 0,
        yes=False,
    )

    assert "将向真实外部渠道发送一条测试消息" in capsys.readouterr().out


def test_product_command_can_hide_technical_argv(interactive_context, capsys) -> None:
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


def test_interactive_command_never_uses_activity_capture(
    interactive_context, monkeypatch
) -> None:
    observed: list[bool] = []

    def execute_with_activity(_execute, _argv, *, label, enabled):
        del label
        observed.append(enabled)
        return 0

    monkeypatch.setattr(
        "kairospy.surface.cli.interactive.execution._execute_with_activity",
        execute_with_activity,
    )

    execute_guided_command(
        interactive_context,
        GuidedCommand(
            ("notifications", "setup"),
            "配置通知",
            execution=CommandExecution.INTERACTIVE,
        ),
        execute=lambda _argv: 0,
        yes=False,
    )

    assert observed == [False]


def test_ctrl_c_during_confirmation_cancels_without_error(
    interactive_context, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "typer.confirm", lambda *args, **kwargs: (_ for _ in ()).throw(click.Abort())
    )

    execute_guided_command(
        interactive_context,
        GuidedCommand(("system", "repair"), "修复", dangerous=True),
        execute=lambda _argv: 0,
        yes=False,
    )

    output = capsys.readouterr().out
    assert "已取消当前操作，未保存任何修改" in output
    assert "Error:" not in output
    assert interactive_context.last_status == 0


def test_ctrl_c_during_interactive_command_returns_to_shell_state(
    interactive_context, capsys
) -> None:
    def interrupted(_argv):
        raise KeyboardInterrupt

    execute_guided_command(
        interactive_context,
        GuidedCommand(
            ("account", "setup"),
            "配置账户",
            execution=CommandExecution.INTERACTIVE,
        ),
        execute=interrupted,
        yes=False,
    )

    assert interactive_context.last_status == 0
    assert "未保存任何修改" in capsys.readouterr().out
