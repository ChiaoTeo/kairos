from __future__ import annotations

from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.business import execution_component
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


def test_launch_selects_instance_before_components(
    interactive_context, tmp_path
) -> None:
    owner = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    registry = LaunchRegistryApplication(owner)
    registry.add("demo", mode="paper", instance_id="run-1")
    registry.add("demo", mode="backtest", instance_id="run-2")
    interactive_context.owner = owner
    interactive_context.shell_path = ("launch", "demo")
    interactive_context.selected_launch = "demo"

    assert launch.handle(interactive_context, ("instances",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("launch", "demo", "instances")
    assert launch.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("launch", "demo", "instances", "run-2")
    assert interactive_context.selected_launch_instance == "run-2"
    assert interactive_context.selected_launch_mode == "backtest"
    assert launch.handle(interactive_context, ("components",)) is ShellControl.HANDLED
    assert interactive_context.shell_path[-1] == "components"
    assert launch.handle(interactive_context, ("execution",)) is ShellControl.HANDLED
    assert interactive_context.shell_path[-1] == "execution"


def test_single_instance_is_auto_selected(interactive_context, tmp_path) -> None:
    owner = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    LaunchRegistryApplication(owner).add("demo", mode="paper", instance_id="only")
    interactive_context.owner = owner
    interactive_context.shell_path = ("launch", "demo")
    interactive_context.selected_launch = "demo"

    assert launch.handle(interactive_context, ("12",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("launch", "demo", "instances", "only")


def test_current_selector_is_replaced_by_real_instance_identity(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("launch", "demo", "instances")
    interactive_context.selected_launch = "demo"
    monkeypatch.setattr(
        launch.LaunchRuntimeApplication,
        "running_instance",
        lambda _self, _launch_id: {
            "launch_id": "demo",
            "instance_id": "run-real",
            "mode": "live",
            "state": "running",
        },
    )
    monkeypatch.setattr(launch, "_print_instance_summary", lambda _context: None)

    assert launch.handle(interactive_context, ("current",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == (
        "launch",
        "demo",
        "instances",
        "run-real",
    )
    assert interactive_context.selected_launch_instance == "run-real"
    assert interactive_context.selected_launch_mode == "live"


def test_execution_component_uses_selected_instance_without_prompt(
    interactive_context,
) -> None:
    interactive_context.shell_path = (
        "launch",
        "demo",
        "instances",
        "run-1",
        "components",
        "execution",
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "run-1"
    interactive_context.selected_launch_mode = "paper"

    command = execution_component.handle(interactive_context, ("open-orders",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "launch",
        "instance",
        "component",
        "execution",
        "open-orders",
        "demo",
        "--instance",
        "run-1",
        "--mode",
        "paper",
        "--format",
        "table",
    )


def test_connected_submit_summary_carries_verified_scope(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = (
        "launch",
        "demo",
        "instances",
        "run-1",
        "components",
        "execution",
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "run-1"
    interactive_context.selected_launch_mode = "live"
    answers = iter(
        ["order-1", "main", "spot", "BTC-USDT", "1", "route-1", "buy", "market"]
    )
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(
        execution_component.account_section,
        "records",
        lambda _context: (
            {
                "account_id": "main",
                "integration_provider": "binance",
                "environment": "live",
            },
        ),
    )

    command = execution_component.handle(interactive_context, ("submit",))

    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
    for expected in (
        "launch=demo",
        "instance=run-1",
        "mode=live",
        "account=main",
        "provider=binance",
        "environment=live",
        "scope=launch-instance",
    ):
        assert expected in command.summary
    assert "--segment-key" in command.argv


def test_connected_cancel_resolves_order_account_before_confirmation(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = (
        "launch",
        "demo",
        "instances",
        "run-1",
        "components",
        "execution",
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "run-1"
    interactive_context.selected_launch_mode = "paper"
    answers = iter(["order-1", "manual cancel"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    seen: list[tuple[str, list[str]]] = []

    def run(_self, component, arguments):
        seen.append((component, list(arguments)))
        return {"order_id": "order-1", "account_id": "main"}

    monkeypatch.setattr(execution_component.NativeCliApplication, "run", run)
    monkeypatch.setattr(
        execution_component.account_section,
        "records",
        lambda _context: (
            {
                "account_id": "main",
                "integration_provider": "binance",
                "environment": "paper",
            },
        ),
    )

    command = execution_component.handle(interactive_context, ("cancel",))

    assert isinstance(command, GuidedCommand)
    assert seen == [
        (
            "execution",
            [
                "connected",
                "--mode",
                "paper",
                "--launch-id",
                "demo",
                "--instance-id",
                "run-1",
                "inspect",
                "--order-id",
                "order-1",
            ],
        )
    ]
    assert "account=main" in command.summary
    assert "scope=launch-instance" in command.summary


def test_instance_timeline_inherits_selected_instance(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = (
        "launch",
        "demo",
        "instances",
        "run-1",
        "timeline",
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "run-1"
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "25")

    timeline = launch.handle(interactive_context, ("list",))

    assert isinstance(timeline, GuidedCommand)
    assert timeline.argv[:5] == ("launch", "instance", "timeline", "list", "demo")
    assert timeline.argv[5] == "run-1"
