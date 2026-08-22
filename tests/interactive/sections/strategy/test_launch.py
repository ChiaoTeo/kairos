from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.strategy import launch


def test_launch_shell_and_preview_share_command_builder(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("launch", "demo")
    interactive_context.selected_launch = "demo"
    launch.print_menu(interactive_context)
    launch.print_help(interactive_context)
    numeric = launch.handle(interactive_context, ("3",))
    text = launch.handle(interactive_context, ("status",))
    built = launch.build_command("demo", "status")
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text == built
    assert "当前 launch" in capsys.readouterr().out


def test_launch_dangerous_and_streaming_attributes() -> None:
    assert launch.build_command("demo", "stop").dangerous is True
    assert launch.build_command("demo", "attach").streaming is True


def test_launch_component_and_timeline_are_real_subcontexts(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = ("launch", "demo")
    interactive_context.selected_launch = "demo"
    assert launch.handle(interactive_context, ("components",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("launch", "demo", "components")
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "instance-1")
    component = launch.handle(interactive_context, ("risk",))
    assert isinstance(component, GuidedCommand)
    assert component.argv[:6] == (
        "launch", "instance", "component", "risk", "status", "demo"
    )

    interactive_context.shell_path = ("launch", "demo")
    assert launch.handle(interactive_context, ("timeline",)) is ShellControl.HANDLED
    answers = iter(["instance-1", "25"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    timeline = launch.handle(interactive_context, ("list",))
    assert isinstance(timeline, GuidedCommand)
    assert timeline.argv[:5] == ("launch", "instance", "timeline", "list", "demo")


def test_launch_market_is_a_connected_subcontext_with_list_selected_instance(
    interactive_context, monkeypatch
) -> None:
    from kairospy.surface.cli.interactive.sections.business import market

    interactive_context.shell_path = ("launch", "demo")
    interactive_context.selected_launch = "demo"
    monkeypatch.setattr(market, "_select_launch_instance", lambda _context: "instance-1")

    assert launch.handle(interactive_context, ("market",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("launch", "demo", "market")
    assert interactive_context.selected_launch_instance == "instance-1"
