from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.investment.apps.account.application import (
    AccountConfigurationDraftApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="draft")


def test_live_account_draft_uses_disposable_workspace_until_atomic_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = AccountConfigurationDraftApplication(workspace)
    draft = application.prepare_live(
        "live-main",
        broker="binance",
        credential_id="binance-main",
        credential_values={"api_key": "staged-key", "api_secret": "staged-secret"},
    )

    assert "staged-key" not in repr(draft)
    assert CredentialConfigurationApplication(workspace).list() == []
    assert list(workspace.paths.account_config().parent.glob("*.toml")) == []
    result = application.test(
        draft,
        probe=lambda account: {
            "capabilities": ["read"],
            "segments": account["segments"],
            "remote_identity": "account-123456",
        },
    )
    assert result["verification_status"] == "verified"
    assert list(workspace.paths.account_config().parent.glob("*.toml")) == []

    committed = application.commit(draft)

    assert committed["verification_status"] == "verified"
    assert (
        CredentialConfigurationApplication(workspace).resolve_field(
            "binance-main", "api_secret"
        )
        == "staged-secret"
    )


def test_failed_account_edit_preserves_available_configuration(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    application = AccountConfigurationDraftApplication(workspace)
    initial = application.prepare_simulated(
        "paper-main", initial_balances=("USD=1000",)
    )
    application.test(initial)
    application.commit(initial)
    account_path = next(workspace.paths.account_config().parent.glob("*.toml"))
    original = account_path.read_text(encoding="utf-8")

    edited = application.prepare_simulated(
        "paper-main", initial_balances=("USD=10",), fee_rate="0.1"
    )
    result = application.test(
        edited,
        probe=lambda _account: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    assert result["verification_status"] == "failed"

    with pytest.raises(ValueError, match="cannot replace"):
        application.commit(edited, overwrite=True, allow_unverified=True)
    assert account_path.read_text(encoding="utf-8") == original


def test_untested_account_draft_requires_explicit_unverified_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = AccountConfigurationDraftApplication(workspace)
    draft = application.prepare_simulated("paper-main")

    with pytest.raises(ValueError, match="allow_unverified"):
        application.commit(draft)

    assert (
        application.commit(draft, allow_unverified=True)["verification_status"]
        == "pending"
    )


def test_discard_cleans_account_sandbox_without_persisting_credential(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    draft = AccountConfigurationDraftApplication(workspace).prepare_live(
        "live-main",
        broker="binance",
        credential_id="binance-main",
        credential_values={"api_key": "discard-key", "api_secret": "discard-secret"},
    )
    sandbox = draft.draft_workspace.paths.root
    staged = draft.prepared_credential
    assert staged is not None

    draft.discard()

    assert sandbox.exists() is False
    assert not (workspace.paths.credentials_root() / "binance-main.toml").exists()
