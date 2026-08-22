from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import order


def test_order_status_routes_through_execution_owner(
    interactive_context, monkeypatch, capsys
) -> None:
    answers = iter(["demo", "instance-1", "order-1"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    order.print_menu(interactive_context)
    order.print_help(interactive_context)
    command = order.handle(interactive_context, ("status",))
    assert isinstance(command, GuidedCommand)
    assert command.argv[:6] == (
        "launch", "instance", "component", "execution", "trace", "demo"
    )
    assert "Execution" in capsys.readouterr().out


def test_order_preview_uses_standalone_evidence(monkeypatch, interactive_context) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "submit-order.json")
    command = order.handle(interactive_context, ("6",))
    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "order", "preview-submit-file", "--file", "submit-order.json"
    )
