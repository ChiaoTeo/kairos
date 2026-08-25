from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.strategy.apps.notification.application import (
    NotificationDestinationDraftApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="draft")


def test_notification_draft_sends_with_staged_secret_then_commits_atomically(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = NotificationDestinationDraftApplication(workspace)
    original = workspace.paths.notification_config().read_text(encoding="utf-8")
    draft = application.prepare(
        "ops",
        provider="telegram",
        credential_id="ops-bot",
        credential_values={"bot_token": "123456:test-token"},
        chat_id="-1001",
    )

    assert "123456:test-token" not in repr(draft)
    assert CredentialConfigurationApplication(workspace).list() == []
    assert workspace.paths.notification_config().read_text(encoding="utf-8") == original

    delivered: list[tuple[str, str]] = []
    result = application.test(
        draft,
        probe=lambda destination, secret: delivered.append(
            (str(destination["chat_id"]), secret)
        ),
    )

    assert result["succeeded"] is True
    assert delivered == [("-1001", "123456:test-token")]
    assert workspace.paths.notification_config().read_text(encoding="utf-8") == original
    committed = application.commit(draft)
    assert committed["verification_status"] == "verified"
    assert (
        CredentialConfigurationApplication(workspace).resolve_field(
            "ops-bot", "bot_token"
        )
        == "123456:test-token"
    )


def test_failed_notification_edit_preserves_available_destination(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = NotificationDestinationDraftApplication(workspace)
    initial = application.prepare(
        "ops",
        provider="telegram",
        credential_values={"bot_token": "123456:working-token"},
        chat_id="-1001",
    )
    application.test(initial, probe=lambda *_args: True)
    application.commit(initial)
    original = workspace.paths.notification_config().read_text(encoding="utf-8")

    edited = application.prepare(
        "ops",
        provider="telegram",
        credential_values={"bot_token": "123456:broken-token"},
        chat_id="-2002",
    )
    assert (
        application.test(
            edited,
            probe=lambda *_args: (_ for _ in ()).throw(ConnectionError("offline")),
        )["succeeded"]
        is False
    )

    with pytest.raises(ValueError, match="cannot replace"):
        application.commit(edited, overwrite=True, allow_unverified=True)
    assert workspace.paths.notification_config().read_text(encoding="utf-8") == original


def test_untested_notification_draft_requires_explicit_unverified_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = NotificationDestinationDraftApplication(workspace)
    draft = application.prepare(
        "ops",
        provider="telegram",
        credential_values={"bot_token": "123456:not-tested"},
        chat_id="-1001",
    )

    with pytest.raises(ValueError, match="allow_unverified"):
        application.commit(draft)

    assert (
        application.commit(draft, allow_unverified=True)["verification_status"]
        == "pending"
    )


def test_discard_does_not_persist_notification_credential(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    draft = NotificationDestinationDraftApplication(workspace).prepare(
        "ops",
        provider="telegram",
        credential_values={"bot_token": "123456:discard-me"},
        chat_id="-1001",
    )
    staged = draft.prepared_credential
    assert staged is not None

    draft.discard()

    assert not (workspace.paths.credentials_root() / "ops.toml").exists()
