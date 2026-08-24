from __future__ import annotations

from kairospy.application.agent import AgentResourceApplication
from kairospy.application.workspace.credentials import SecretRef
from kairospy.application.notification import (
    NotificationAdminApplication,
    NotificationSecretRef,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.interactive.context import go_back
from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.business import data_connections
from kairospy.surface.cli.interactive.sections.getting_started import resources


def test_resource_center_exposes_all_workspace_connection_types(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("resources",)

    resources.print_menu(interactive_context)

    text = capsys.readouterr().out
    for label in ("交易账户", "市场数据", "AI 模型", "通知提醒", "检查所有连接"):
        assert label in text


def test_resource_center_owns_nested_navigation(interactive_context) -> None:
    interactive_context.shell_path = ("resources",)

    assert resources.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("resources", "accounts")
    go_back(interactive_context)
    assert interactive_context.shell_path == ("resources",)
    assert resources.handle(interactive_context, ("3",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("resources", "models")


def test_resource_summary_counts_only_manually_verified_connections(
    interactive_context, tmp_path, monkeypatch
) -> None:
    owner = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    interactive_context.owner = owner
    monkeypatch.setenv("OPENAI_RESOURCE_KEY", "openai-secret")
    monkeypatch.setenv("TELEGRAM_RESOURCE_TOKEN", "12345:test-token")
    models = AgentResourceApplication(owner)
    models.configure_openai_credential(
        "openai-main", SecretRef("env", "OPENAI_RESOURCE_KEY")
    )
    models.test_openai_model("openai-main", "gpt-5.4-2026-08-01", probe=lambda *_: None)
    notifications = NotificationAdminApplication(owner)
    notifications.configure(
        "telegram-ops",
        provider="telegram",
        secret_ref=NotificationSecretRef("env", "TELEGRAM_RESOURCE_TOKEN"),
        chat_id="100",
    )

    before = resources.resource_summary(interactive_context)
    notifications.record_test("telegram-ops", succeeded=True)
    after = resources.resource_summary(interactive_context)

    assert before["models"] == {"count": 1, "verified": 1, "needs_action": 0}
    assert before["notifications"] == {
        "count": 1,
        "verified": 0,
        "needs_action": 1,
    }
    assert after["notifications"] == {
        "count": 1,
        "verified": 1,
        "needs_action": 0,
    }


def test_data_connection_detail_exposes_owner_lifecycle_actions(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("resources", "data")
    monkeypatch.setattr(
        data_connections,
        "_connections",
        lambda _context: [
            {
                "connection_id": "massive",
                "verification_status": "verified",
                "credential_id": "massive-readonly",
                "capabilities": ["reference", "equity_market"],
            }
        ],
    )

    assert data_connections.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("resources", "data", "massive")
    data_connections.print_menu(interactive_context)
    assert "5. 删除" in capsys.readouterr().out
    disabled = data_connections.handle(interactive_context, ("disable",))
    deleted = data_connections.handle(interactive_context, ("delete",))
    assert isinstance(disabled, GuidedCommand) and disabled.dangerous is True
    assert isinstance(deleted, GuidedCommand) and deleted.dangerous is True
