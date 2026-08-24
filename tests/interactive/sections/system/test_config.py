from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.system import config


def test_config_menu_help_and_show_aliases(interactive_context, capsys) -> None:
    config.print_menu(interactive_context)
    config.print_help(interactive_context)
    numeric = config.handle(interactive_context, ("3",))
    text = config.handle(interactive_context, ("show",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert "高级配置" in capsys.readouterr().out


def test_profile_use_is_dangerous(interactive_context, monkeypatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "paper")
    command = config.handle(interactive_context, ("use",))
    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True


def test_agent_resource_commands_are_exposed_from_system_config_menu(
    interactive_context,
) -> None:
    status = config.handle(interactive_context, ("agent",))
    setup = config.handle(interactive_context, ("agent-setup",))

    assert status == GuidedCommand(
        ("config", "agent", "status", "--format", "text"),
        "查看 Workspace AI 模型连接",
    )
    assert setup == GuidedCommand(
        ("config", "agent", "setup"),
        "配置 Workspace AI 模型连接；Agent 策略和工具权限归具体运行方案",
        dangerous=True,
    )
