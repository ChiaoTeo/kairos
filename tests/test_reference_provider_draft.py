from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
)
from kairospy.application.reference import ReferenceProviderDraftApplication
from kairospy.application.workspace import WorkspaceApplication


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="draft")


def test_reference_draft_tests_staged_settings_then_commits_atomically(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = ReferenceProviderDraftApplication(workspace)
    original_manifest = workspace.paths.manifest.read_text(encoding="utf-8")
    draft = application.prepare_massive(
        credential_id="massive-readonly",
        credential_values={"api_key": "staged-massive-key"},
        endpoint="https://massive.example.test",
    )

    assert "staged-massive-key" not in repr(draft)
    assert "staged-massive-key" not in repr(draft.redacted_summary())
    assert CredentialConfigurationApplication(workspace).list() == []
    assert workspace.paths.manifest.read_text(encoding="utf-8") == original_manifest

    result = application.test(
        draft,
        probe=lambda endpoint, key: {
            "reference_symbol": "AAPL",
            "market_symbol": "SPY",
            "bar_count": int(
                endpoint == "https://massive.example.test"
                and key == "staged-massive-key"
            ),
        },
    )
    assert result["succeeded"] is True
    assert workspace.paths.manifest.read_text(encoding="utf-8") == original_manifest

    committed = application.commit(draft)

    assert committed["verification_status"] == "verified"
    assert (
        CredentialConfigurationApplication(workspace).resolve_field(
            "massive-readonly", "api_key"
        )
        == "staged-massive-key"
    )


def test_failed_edit_does_not_replace_available_reference_connection(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = ReferenceProviderDraftApplication(workspace)
    initial = application.prepare_massive(
        credential_id="massive-readonly",
        credential_values={"api_key": "working-key"},
    )
    application.test(initial, probe=lambda *_args: {"bar_count": 1})
    application.commit(initial)
    original_manifest = workspace.paths.manifest.read_text(encoding="utf-8")

    edited = application.prepare_massive(
        credential_id="massive-readonly",
        credential_values={"api_key": "broken-key"},
        endpoint="https://new.massive.example.test",
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
    assert workspace.paths.manifest.read_text(encoding="utf-8") == original_manifest


def test_untested_reference_draft_requires_explicit_unverified_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = ReferenceProviderDraftApplication(workspace)
    draft = application.prepare_massive(
        credential_id="massive-readonly",
        credential_values={"api_key": "not-tested"},
    )

    with pytest.raises(ValueError, match="allow_unverified"):
        application.commit(draft)

    assert (
        application.commit(draft, allow_unverified=True)["verification_status"]
        == "pending"
    )


def test_discard_removes_unreferenced_reference_secret(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    draft = ReferenceProviderDraftApplication(workspace).prepare_massive(
        credential_id="massive-readonly",
        credential_values={"api_key": "discard-me"},
    )
    staged = draft.prepared_credential
    assert staged is not None and staged.staged_secret_root is not None
    secret_root = staged.staged_secret_root

    draft.discard()

    assert secret_root.exists() is False
