from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import order


def _enter_orders(interactive_context) -> None:
    interactive_context.shell_path = ("trade", "accounts", "paper-main", "orders")
    interactive_context.selected_account = "paper-main"


def test_order_open_orders_is_account_scoped_standalone(
    interactive_context, capsys
) -> None:
    _enter_orders(interactive_context)
    order.print_menu(interactive_context)

    command = order.handle(interactive_context, ("open-orders",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "order",
        "open-orders",
        "--account-id",
        "paper-main",
        "--format",
        "table",
    )
    assert interactive_context.shell_path[-1] == "open"
    assert "直接连接交易所" in capsys.readouterr().out


def test_order_lookup_enters_concrete_order_context(
    interactive_context, monkeypatch
) -> None:
    _enter_orders(interactive_context)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "order-1")

    command = order.handle(interactive_context, ("order",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "order",
        "order",
        "--account-id",
        "paper-main",
        "--order-id",
        "order-1",
        "--format",
        "table",
    )
    assert interactive_context.selected_order == "order-1"
    assert interactive_context.shell_path[-1] == "order-1"


def test_order_write_actions_are_dangerous(interactive_context, monkeypatch) -> None:
    _enter_orders(interactive_context)
    answers = iter(["order-1", "BTC-USDT", "BTCUSDT", "1", "buy", "market"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    command = order.handle(interactive_context, ("submit",))

    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
    assert command.argv[:4] == ("order", "submit", "--account-id", "paper-main")


def test_order_rejects_entry_without_account(interactive_context, capsys) -> None:
    interactive_context.shell_path = ("trade", "accounts")
    assert order.handle(interactive_context, ("open-orders",)).name == "HANDLED"
    assert "请先" in capsys.readouterr().out


def test_open_order_can_be_selected_by_index_without_copying_id(
    interactive_context, monkeypatch
) -> None:
    _enter_orders(interactive_context)
    interactive_context.owner = object()
    interactive_context.shell_path = (
        "trade",
        "accounts",
        "paper-main",
        "orders",
        "open",
    )
    monkeypatch.setattr(
        order.AccountCliApplication,
        "run",
        lambda _self, _arguments: {"account_id": "paper-main"},
    )

    class Result:
        returncode = 0
        stderr = ""
        stdout = (
            '{"orders":['
            '{"order_id":"order-1","remote_order_id":"100","symbol":"BTCUSDT"},'
            '{"order_id":"order-2","remote_order_id":"101","symbol":"ETHUSDT"}'
            "]}"
        )

    monkeypatch.setattr(
        order.NativeCliApplication,
        "invoke",
        lambda _self, _component, _arguments: Result(),
    )
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "2")

    command = order.handle(interactive_context, ("select",))

    assert isinstance(command, GuidedCommand)
    assert interactive_context.selected_order == "order-2"
    assert interactive_context.selected_order_symbol == "ETHUSDT"
    assert interactive_context.shell_path[-1] == "order-2"
    assert command.argv[-4:] == ("--symbol", "ETHUSDT", "--format", "table")
