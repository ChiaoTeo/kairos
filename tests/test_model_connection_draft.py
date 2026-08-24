from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.strategy.apps.agent.application import ModelConnectionDraftApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="draft")


def test_model_draft_tests_in_memory_then_commits_credential_and_connection(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = ModelConnectionDraftApplication(workspace)
    draft = application.prepare(
        "gateway-main",
        provider="custom",
        api_mode="openai-chat-completions",
        base_url="https://models.example.test/v1",
        credential_id="gateway-auth",
        credential_values={"api_key": "secret-draft-value"},
        models=("company/model-a",),
    )

    assert "secret-draft-value" not in repr(draft)
    assert "secret-draft-value" not in repr(draft.redacted_summary())
    assert CredentialConfigurationApplication(workspace).list() == []
    assert workspace.paths.model_connections_root().exists() is False
    assert (
        application.test(
            draft,
            "company/model-a",
            probe=lambda connection, secret, model: (
                connection["base_url"],
                secret == "secret-draft-value",
                model,
            ),
        )["succeeded"]
        is True
    )

    result = application.commit(draft)

    assert result["verification_status"] == "verified"
    assert result["model_ref"] == "gateway-main/company/model-a"
    assert (
        CredentialConfigurationApplication(workspace).resolve_field(
            "gateway-auth", "api_key"
        )
        == "secret-draft-value"
    )


def test_failed_edit_does_not_replace_available_model_connection(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    application = ModelConnectionDraftApplication(workspace)
    initial = application.prepare(
        "local-main",
        provider="ollama",
        models=("qwen3:8b",),
    )
    application.test(initial, "qwen3:8b", probe=lambda *_args: {"ok": True})
    application.commit(initial)
    path = workspace.paths.model_connections_root() / "local-main.toml"
    old_document = path.read_text(encoding="utf-8")

    edited = application.prepare(
        "local-main",
        provider="ollama",
        base_url="http://127.0.0.1:22434/v1",
        models=("qwen3:8b",),
    )

    def fail(*_args):
        raise ConnectionError("unreachable")

    assert application.test(edited, "qwen3:8b", probe=fail)["succeeded"] is False
    with pytest.raises(ValueError, match="cannot replace"):
        application.commit(edited, overwrite=True, allow_unverified=True)
    assert path.read_text(encoding="utf-8") == old_document
    assert edited.committed is False


def test_untested_draft_requires_explicit_unverified_commit(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    application = ModelConnectionDraftApplication(workspace)
    draft = application.prepare("local-main", provider="ollama", models=("qwen3:8b",))

    with pytest.raises(ValueError, match="allow_unverified"):
        application.commit(draft)

    result = application.commit(draft, allow_unverified=True)
    assert result["verification_status"] == "pending"


def test_discard_removes_unreferenced_staged_secret(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    draft = ModelConnectionDraftApplication(workspace).prepare(
        "hosted",
        provider="openai",
        credential_id="hosted-auth",
        credential_values={"api_key": "discard-me"},
    )
    staged = draft.prepared_credential
    assert staged is not None and staged.staged_secret_root is not None
    secret_root = staged.staged_secret_root

    draft.discard()

    assert secret_root.exists() is False
