from __future__ import annotations

from types import SimpleNamespace

from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.business import market


def _market_record():
    return SimpleNamespace(
        id="market:binance:spot:BTCUSDT",
        venue_symbol="BTCUSDT",
        exchange_id="exchange:binance",
        instrument_kind="spot",
        instrument=SimpleNamespace(
            id="instrument:crypto:BTCUSDT", display_symbol="BTC/USDT"
        ),
    )


def _sources():
    return {
        "sources": [
            {
                "source_id": "binance-spot",
                "provider_id": "binance",
                "observation_capabilities": ["quote", "bar"],
                "configured": True,
                "status": "ready",
                "ready": True,
            }
        ]
    }


def test_system_market_snapshot_numeric_and_text_alias_use_list_selections(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("system", "market")
    record = _market_record()
    monkeypatch.setattr(market.reference, "select_market", lambda _context: record)
    monkeypatch.setattr(market, "_load_sources", lambda *_args: _sources())
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "1")

    market.print_menu(interactive_context)
    market.print_help(interactive_context)
    numeric = market.handle(interactive_context, ("3",))
    text = market.handle(interactive_context, ("quote",))

    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv
    assert numeric.argv == (
        "system",
        "component",
        "market",
        "snapshot",
        "quote",
        "--market-id",
        "market:binance:spot:BTCUSDT",
        "--source-id",
        "binance-spot",
        "--format",
        "table",
    )
    output = capsys.readouterr().out
    assert "workspace 共享服务（连接模式）" in output
    assert "只能从当前作用域返回的列表中选择" in output


def test_market_source_cancel_never_constructs_snapshot(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = ("system", "market")
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    monkeypatch.setattr(market, "_load_sources", lambda *_args: _sources())
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "b")

    assert market.handle(interactive_context, ("quote",)) is ShellControl.HANDLED


def test_launch_market_command_keeps_selected_instance_and_scope(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = (
        "launch", "demo", "instances", "instance-1", "components", "market"
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "instance-1"
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    monkeypatch.setattr(market, "_load_sources", lambda *_args: _sources())
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "1")

    command = market.handle(interactive_context, ("quote",))

    assert isinstance(command, GuidedCommand)
    assert command.argv[:9] == (
        "launch",
        "instance",
        "component",
        "market",
        "snapshot",
        "demo",
        "quote",
        "--instance",
        "instance-1",
    )


def test_connected_market_has_no_subscription_or_raw_identity_entry(
    interactive_context
) -> None:
    interactive_context.shell_path = ("system", "market")
    assert market.handle(interactive_context, ("subscribe",)) is None
    assert market.handle(interactive_context, ("market:binance:spot:BTCUSDT",)) is None


def test_source_discovery_uses_scope_adapter_with_typed_filters(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("system", "market")
    seen = {}

    def run(owner, command, arguments):
        seen.update(owner=owner, command=command, arguments=arguments)
        return _sources()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.root._run_workspace_market_connected_command",
        run,
    )

    assert market._load_sources(
        interactive_context, "market:binance:spot:BTCUSDT", "quote"
    ) == _sources()
    assert seen == {
        "owner": interactive_context.owner,
        "command": "sources",
        "arguments": [
            "--market-id",
            "market:binance:spot:BTCUSDT",
            "--observation-kind",
            "quote",
            "--configured-only",
        ],
    }


def test_launch_instance_is_selected_only_from_registry_and_persisted(
    interactive_context, tmp_path, monkeypatch
) -> None:
    owner = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    LaunchRegistryApplication(owner).add("demo", mode="paper", instance_id="run-1")
    LaunchRegistryApplication(owner).add("demo", mode="paper", instance_id="run-2")
    interactive_context.owner = owner
    interactive_context.selected_launch = "demo"
    interactive_context.shell_path = ("launch", "demo")
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "2")

    assert market.enter_launch_market(interactive_context) is ShellControl.HANDLED
    assert interactive_context.selected_launch_instance == "run-2"
    assert interactive_context.shell_path == (
        "launch", "demo", "instances", "run-2", "components", "market"
    )
    assert market._select_launch_instance(interactive_context) == "run-2"


def test_preview_command_contains_explicit_system_scope(
    interactive_context, monkeypatch
) -> None:
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    monkeypatch.setattr(market, "_load_sources", lambda *_args: _sources())
    answers = iter(["2", "1", "1"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    command = market.choose(interactive_context)

    assert command.argv[:5] == (
        "system",
        "component",
        "market",
        "snapshot",
        "quote",
    )
    assert interactive_context.shell_path == ("system", "market")


def test_top_level_market_once_uses_standalone_provider(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market",)
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    answers = iter(["1", "1"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    command = market.handle(interactive_context, ("once",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "market",
        "once",
        "--market-id",
        "market:binance:spot:BTCUSDT",
        "--instrument-id",
        "instrument:crypto:BTCUSDT",
        "--exchange-id",
        "binance",
        "--market-type",
        "spot",
        "--source-symbol",
        "BTCUSDT",
        "--provider",
        "binance-spot-rest",
        "--format",
        "table",
    )
    assert "system" not in command.argv
    assert "source-id" not in " ".join(command.argv)


def test_direct_market_menu_presents_user_tasks_in_product_order(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("market",)

    market.print_menu(interactive_context)

    output = capsys.readouterr().out
    assert "Market 行情" in output
    assert "1. 查看实时行情" in output
    assert "2. 下载历史行情" in output
    assert "3. 查看本地行情文件" in output
    assert "d. 诊断工具" in output
    assert "验证市场定义" not in output
    assert "Reference → Market" not in output


def test_market_diagnostics_are_separate_from_user_tasks(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("market",)

    assert market.handle(interactive_context, ("d",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("market", "diagnostics")

    market.print_menu(interactive_context)
    output = capsys.readouterr().out
    assert "Market 诊断" in output
    assert "1. 验证市场定义" in output
    assert "2. 检查 Reference → Market 映射" in output


def test_connected_market_replay_controls_target_runtime(
    interactive_context,
) -> None:
    interactive_context.shell_path = ("system", "market")
    pause = market.handle(interactive_context, ("p",))
    resume = market.handle(interactive_context, ("resume",))

    assert isinstance(pause, GuidedCommand)
    assert pause.argv == (
        "system",
        "component",
        "market",
        "pause-replay",
        "--format",
        "json",
    )
    assert isinstance(resume, GuidedCommand)
    assert resume.argv[3] == "resume-replay"


def test_launch_market_replay_control_keeps_instance_scope(
    interactive_context,
) -> None:
    interactive_context.shell_path = (
        "launch", "demo", "instances", "instance-1", "components", "market"
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "instance-1"

    command = market.handle(interactive_context, ("pause",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "launch",
        "instance",
        "component",
        "market",
        "pause-replay",
        "demo",
        "--instance",
        "instance-1",
        "--format",
        "json",
    )
