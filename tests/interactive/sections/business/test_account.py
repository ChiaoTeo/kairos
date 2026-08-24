from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import account


def test_account_selected_context_builds_owned_command(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("trade", "accounts", "paper-main")
    interactive_context.selected_account = "paper-main"
    interactive_context.selected_account_segment = "spot"
    account.print_menu(interactive_context)
    account.print_help(interactive_context)
    command = account.handle(interactive_context, ("2",))
    assert isinstance(command, GuidedCommand)
    assert command.argv == ("account", "assets", "paper-main", "--format", "table")
    assert interactive_context.shell_path == (
        "trade",
        "accounts",
        "paper-main",
        "assets",
    )
    assert "当前账户" in capsys.readouterr().out


def test_resource_account_list_exposes_guided_setup(interactive_context) -> None:
    interactive_context.shell_path = ("resources", "accounts")

    command = account.handle(interactive_context, ("new",))

    assert isinstance(command, GuidedCommand)
    assert command.argv == ("account", "setup")
    assert command.dangerous is True


def test_account_list_makes_live_readonly_risk_visible(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("resources", "accounts")
    monkeypatch.setattr(
        account,
        "records",
        lambda _context: (
            {
                "account_id": "live-readonly",
                "broker": "binance",
                "environment": "live",
                "credential_role": "readonly",
                "segments": ["spot"],
            },
        ),
    )

    account.print_menu(interactive_context)

    output = capsys.readouterr().out
    assert "类型/权限" in output
    assert "实盘 · 只读" in output


def test_account_detail_exposes_lifecycle_actions(interactive_context) -> None:
    interactive_context.shell_path = ("resources", "accounts", "paper-main")
    interactive_context.selected_account = "paper-main"

    enabled = account.handle(interactive_context, ("enable",))
    disabled = account.handle(interactive_context, ("disable",))
    deleted = account.handle(interactive_context, ("delete",))

    assert isinstance(enabled, GuidedCommand)
    assert enabled.argv[:5] == (
        "account",
        "modify",
        "--account-id",
        "paper-main",
        "--status",
    )
    assert enabled.argv[5] == "configured"
    assert isinstance(disabled, GuidedCommand)
    assert disabled.argv[5] == "disabled"
    assert disabled.dangerous is True
    assert isinstance(deleted, GuidedCommand)
    assert deleted.argv[:4] == (
        "account",
        "remove",
        "--account-id",
        "paper-main",
    )
    assert deleted.dangerous is True


def test_account_text_alias_matches_numeric(interactive_context) -> None:
    interactive_context.shell_path = ("trade", "accounts", "paper-main")
    interactive_context.selected_account = "paper-main"
    numeric = account.handle(interactive_context, ("3",))
    interactive_context.shell_path = ("trade", "accounts", "paper-main")
    text = account.handle(interactive_context, ("positions",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv


def test_account_orders_enter_execution_standalone_context(interactive_context) -> None:
    interactive_context.shell_path = ("trade", "accounts", "paper-main")
    interactive_context.selected_account = "paper-main"
    interactive_context.selected_account_segment = "spot"

    result = account.handle(interactive_context, ("4",))

    assert result.name == "HANDLED"
    assert interactive_context.shell_path == (
        "trade",
        "accounts",
        "paper-main",
        "orders",
    )


def test_switch_clears_account_context(interactive_context) -> None:
    interactive_context.shell_path = ("trade", "accounts", "paper-main")
    interactive_context.selected_account = "paper-main"
    interactive_context.selected_order = "order-1"

    account.handle(interactive_context, ("switch",))

    assert interactive_context.shell_path == ("trade", "accounts")
    assert interactive_context.selected_account is None
    assert interactive_context.selected_order is None


def test_account_workbench_shows_target_identity_and_availability(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("trade", "accounts", "live-main")
    interactive_context.selected_account = "live-main"
    monkeypatch.setattr(
        account,
        "records",
        lambda _context: (
            {
                "account_id": "live-main",
                "alias": "主交易账户",
                "integration_provider": "binance",
                "environment": "live",
                "segments": ["primary"],
                "products": ["spot"],
                "status": "connected",
            },
        ),
    )

    account.print_menu(interactive_context)

    text = capsys.readouterr().out
    for expected in (
        "live-main",
        "主交易账户",
        "binance",
        "产品：spot",
        "分区：primary",
        "live",
        "连接可用性：可用",
        "账户状态：connected",
    ):
        assert expected in text


def test_configured_account_does_not_claim_a_live_connection(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("trade", "accounts", "live-main")
    interactive_context.selected_account = "live-main"
    monkeypatch.setattr(
        account,
        "records",
        lambda _context: (
            {
                "account_id": "live-main",
                "integration_provider": "binance",
                "environment": "live",
                "segments": ["spot"],
                "products": ["spot"],
                "status": "configured",
            },
        ),
    )

    account.print_menu(interactive_context)

    assert "连接可用性：未探测（账户状态：configured）" in capsys.readouterr().out
