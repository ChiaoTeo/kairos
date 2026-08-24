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


def _equity_market_record():
    return SimpleNamespace(
        id="market:nasdaq:equity:AAPL:USD",
        venue_symbol="AAPL",
        exchange_id="exchange:nasdaq",
        instrument_kind="equity",
        instrument=SimpleNamespace(
            id="instrument:equity:US:AAPL:common", display_symbol="AAPL"
        ),
    )


def _routes():
    return {
        "routes": [
            {
                "provider": "binance",
                "observation_kinds": ["quote", "bar"],
                "state": "ready",
                "selected": True,
                "pending_reason": None,
            }
        ]
    }


def _direct_routes(*providers: str):
    return {"routes": [{"provider": provider} for provider in providers]}


def test_system_market_snapshot_numeric_and_text_alias_use_list_selections(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("system", "market")
    record = _market_record()
    monkeypatch.setattr(market.reference, "select_market", lambda _context: record)
    monkeypatch.setattr(market, "_load_routes", lambda *_args: _routes())
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
        "--provider",
        "binance",
        "--format",
        "table",
    )
    output = capsys.readouterr().out
    assert "workspace 共享服务（连接模式）" in output
    assert "只能从当前作用域返回的 route 列表中选择" in output


def test_market_provider_cancel_never_constructs_snapshot(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = ("system", "market")
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    monkeypatch.setattr(market, "_load_routes", lambda *_args: _routes())
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "b")

    assert market.handle(interactive_context, ("quote",)) is ShellControl.HANDLED


def test_launch_market_command_keeps_selected_instance_and_scope(
    interactive_context, monkeypatch
) -> None:
    interactive_context.shell_path = (
        "launch",
        "demo",
        "instances",
        "instance-1",
        "components",
        "market",
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "instance-1"
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    monkeypatch.setattr(market, "_load_routes", lambda *_args: _routes())
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
    interactive_context,
) -> None:
    interactive_context.shell_path = ("system", "market")
    assert market.handle(interactive_context, ("subscribe",)) is None
    assert market.handle(interactive_context, ("market:binance:spot:BTCUSDT",)) is None


def test_route_discovery_uses_scope_adapter_with_typed_filters(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("system", "market")
    seen = {}

    def run(owner, command, arguments):
        seen.update(owner=owner, command=command, arguments=arguments)
        return _routes()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.root._run_workspace_market_connected_command",
        run,
    )

    assert (
        market._load_routes(
            interactive_context, "market:binance:spot:BTCUSDT", "quote"
        )
        == _routes()
    )
    assert seen == {
        "owner": interactive_context.owner,
        "command": "routes",
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
        "launch",
        "demo",
        "instances",
        "run-2",
        "components",
        "market",
    )
    assert market._select_launch_instance(interactive_context) == "run-2"


def test_preview_command_contains_explicit_system_scope(
    interactive_context, monkeypatch
) -> None:
    monkeypatch.setattr(
        market.reference, "select_market", lambda _context: _market_record()
    )
    monkeypatch.setattr(market, "_load_routes", lambda *_args: _routes())
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
    allowed_kinds = None

    def select_market(
        _context, *, allowed_instrument_kinds=None, availability_label="行情查询"
    ):
        nonlocal allowed_kinds
        allowed_kinds = allowed_instrument_kinds
        assert availability_label == "实时行情"
        return _market_record()

    monkeypatch.setattr(market.reference, "select_market", select_market)
    monkeypatch.setattr(
        market,
        "_load_direct_routes",
        lambda *_args: _direct_routes("binance"),
    )
    answers = iter(["1"])
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
        "--symbol",
        "BTCUSDT",
        "--provider",
        "binance",
        "--observation-kind",
        "quote",
        "--format",
        "table",
    )
    assert "system" not in command.argv
    assert "--provider" in command.argv
    assert allowed_kinds is None
    assert command.show_command is False


def test_direct_market_menu_presents_user_tasks_in_product_order(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("market",)

    market.print_menu(interactive_context)

    output = capsys.readouterr().out
    assert "行情中心" in output
    assert "1. 搜索标的并查看行情" in output
    assert "2. 下载历史行情" in output
    assert "3. 查看本地行情数据" in output
    assert "c. 连接运行中的行情服务" in output
    assert "d. 诊断问题" in output
    assert "a. 高级：输入完整市场标识" in output
    assert "验证市场定义" not in output
    assert "Reference → Market" not in output


def test_local_market_data_lists_catalog_instead_of_starting_replay(
    interactive_context,
) -> None:
    interactive_context.shell_path = ("market",)

    command = market.handle(interactive_context, ("3",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == ("market", "datasets", "--format", "table")
    assert command.summary == "查看本地行情数据"


def test_direct_market_once_does_not_ask_for_description_strategy(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market",)
    monkeypatch.setattr(
        market.reference,
        "select_market",
        lambda _context, **_kwargs: _market_record(),
    )
    monkeypatch.setattr(
        market,
        "_load_direct_routes",
        lambda *_args: _direct_routes("binance"),
    )
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "1")

    command = market.handle(interactive_context, ("once",))

    assert isinstance(command, GuidedCommand)
    output = capsys.readouterr().out
    assert "选择 Market 描述方式" not in output
    assert "手动输入底层描述" not in output
    assert "你想查看" in output
    assert "最新报价" in output
    assert "最近成交" in output
    assert "买卖盘口" in output
    assert "REST" not in output
    assert "WebSocket" not in output


def test_direct_market_once_offers_all_configured_equity_quote_routes(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market",)
    allowed_kinds = "not-called"

    def select_market(
        _context, *, allowed_instrument_kinds=None, availability_label="行情查询"
    ):
        nonlocal allowed_kinds
        allowed_kinds = allowed_instrument_kinds
        return _equity_market_record()

    monkeypatch.setattr(market.reference, "select_market", select_market)
    monkeypatch.setattr(
        market,
        "_load_direct_routes",
        lambda *_args: _direct_routes("binance", "massive"),
    )
    answers = iter(["1", "1"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    result = market.handle(interactive_context, ("once",))

    assert isinstance(result, GuidedCommand)
    assert allowed_kinds is None
    assert result.argv == (
        "market",
        "once",
        "--market-id",
        "market:nasdaq:equity:AAPL:USD",
        "--instrument-id",
        "instrument:equity:US:AAPL:common",
        "--exchange-id",
        "nasdaq",
        "--market-type",
        "equity",
        "--symbol",
        "AAPL",
        "--provider",
        "binance",
        "--observation-kind",
        "quote",
        "--format",
        "table",
    )
    output = capsys.readouterr().out
    assert "最新报价" in output
    assert "最近成交" in output
    assert "最新分钟 K" in output
    assert "可用 Provider" in output
    assert "1. binance" in output
    assert "2. massive" in output
    assert interactive_context.shell_path == ("market", "AAPL")
    assert interactive_context.selected_market_provider == {
        "provider": "binance",
    }


def test_direct_market_context_reuses_target_and_provider_for_next_query(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market", "AAPL")
    interactive_context.selected_market = _equity_market_record()
    interactive_context.selected_market_provider = {
        "provider": "massive",
    }
    monkeypatch.setattr(
        market.reference,
        "select_market",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("当前标的上下文不应重新搜索")
        ),
    )
    monkeypatch.setattr(
        market,
        "_load_direct_routes",
        lambda *_args: _direct_routes("massive"),
    )

    market.print_menu(interactive_context)
    command = market.handle(interactive_context, ("3",))

    assert isinstance(command, GuidedCommand)
    assert command.summary == "查看 AAPL 最新分钟 K"
    assert "bar" in command.argv
    assert interactive_context.shell_path == ("market", "AAPL")
    output = capsys.readouterr().out
    assert "当前标的：AAPL · nasdaq · equity" in output
    assert "数据 Provider：massive" in output
    assert "继续使用当前 Provider：massive" in output


def test_direct_market_context_can_switch_target_without_leaving_market(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market", "BTCUSDT")
    interactive_context.selected_market = _market_record()
    interactive_context.selected_market_provider = {
        "provider": "binance",
    }
    monkeypatch.setattr(
        market.reference,
        "select_market",
        lambda _context, **_kwargs: _equity_market_record(),
    )
    monkeypatch.setattr(
        market,
        "_load_direct_routes",
        lambda *_args: _direct_routes("massive"),
    )
    monkeypatch.setattr("typer.prompt", lambda *_args, **_kwargs: "1")

    command = market.handle(interactive_context, ("s",))

    assert isinstance(command, GuidedCommand)
    assert command.summary == "查看 AAPL 最新报价"
    assert interactive_context.shell_path == ("market", "AAPL")
    assert interactive_context.selected_market is not None
    assert interactive_context.selected_market_provider == {
        "provider": "massive",
    }


def test_direct_market_once_skips_provider_prompt_when_only_massive_supports_trade(
    interactive_context, monkeypatch
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market",)
    monkeypatch.setattr(
        market.reference,
        "select_market",
        lambda _context, **_kwargs: _equity_market_record(),
    )
    monkeypatch.setattr(
        market,
        "_load_direct_routes",
        lambda *_args: _direct_routes("massive"),
    )
    prompts = []

    def prompt(label, **_kwargs):
        prompts.append(label)
        return "2"

    monkeypatch.setattr("typer.prompt", prompt)

    result = market.handle(interactive_context, ("once",))

    assert isinstance(result, GuidedCommand)
    assert "--provider" in result.argv
    assert "massive" in result.argv
    assert prompts == ["请输入序号；输入 b 返回"]


def test_history_download_starts_from_market_and_hides_canonical_ids(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market",)
    allowed_kinds = None

    def select_market(
        _context, *, allowed_instrument_kinds=None, availability_label="行情查询"
    ):
        nonlocal allowed_kinds
        allowed_kinds = allowed_instrument_kinds
        assert availability_label == "历史行情"
        return _market_record()

    monkeypatch.setattr(market.reference, "select_market", select_market)
    prompts = []
    answers = iter(["1", "2026-08-01", "2026-08-02", "prices.jsonl", "1h"])

    def prompt(label, **_kwargs):
        prompts.append(label)
        return next(answers)

    monkeypatch.setattr("typer.prompt", prompt)

    command = market.handle(interactive_context, ("download",))

    assert isinstance(command, GuidedCommand)
    assert allowed_kinds == ("spot", "equity", "option")
    assert command.argv == (
        "market",
        "download",
        "--provider",
        "binance",
        "--symbol",
        "BTCUSDT",
        "--market-type",
        "spot",
        "--data-kind",
        "bar",
        "--instrument-id",
        "instrument:crypto:BTCUSDT",
        "--start",
        "1785542400000",
        "--end",
        "1785715199999",
        "--file",
        "prices.jsonl",
        "--market-id",
        "market:binance:spot:BTCUSDT",
        "--interval",
        "1h",
        "--format",
        "table",
    )
    assert prompts == [
        "请输入序号；输入 b 返回",
        "开始日期",
        "结束日期",
        "保存位置",
        "K 线周期",
    ]
    assert "Canonical" not in capsys.readouterr().out


def test_history_download_rejects_reversed_date_range(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.owner = object()
    interactive_context.shell_path = ("market",)
    monkeypatch.setattr(
        market.reference,
        "select_market",
        lambda _context, **_kwargs: _market_record(),
    )
    answers = iter(["1", "2026-08-03", "2026-08-02"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    result = market.handle(interactive_context, ("download",))

    assert result is ShellControl.HANDLED
    assert "开始日期必须早于结束日期" in capsys.readouterr().out


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
        "launch",
        "demo",
        "instances",
        "instance-1",
        "components",
        "market",
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
