from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from kairospy.application.credential import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def test_credential_configuration_persists_only_field_secret_refs(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    app = CredentialConfigurationApplication(workspace)
    monkeypatch.setenv("OPENAI_TEST_API_KEY", "sk-do-not-persist")

    summary = app.configure(
        "openai-main",
        provider="openai",
        fields={"api_key": SecretRef("env", "OPENAI_TEST_API_KEY")},
    )

    path = workspace.paths.credential_config().parent / "openai-main.toml"
    text = path.read_text(encoding="utf-8")
    assert "sk-do-not-persist" not in text
    assert "[credential.fields.api_key]" in text
    assert summary["configured"] is True
    assert summary["secret_refs"] == {
        "api_key": {"source": "env", "id": "OPENAI_TEST_API_KEY"}
    }
    assert app.resolve_field("openai-main", "api_key") == "sk-do-not-persist"
    assert "sk-do-not-persist" not in repr(summary)


def test_file_secret_ref_is_workspace_relative_and_summary_is_secret_safe(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    secret = workspace.paths.root / "secrets" / "telegram-token"
    secret.parent.mkdir(parents=True)
    secret.write_text("telegram-secret\n", encoding="utf-8")
    app = CredentialConfigurationApplication(workspace)

    summary = app.configure(
        "telegram-ops",
        provider="telegram",
        fields={"bot_token": SecretRef("file", "secrets/telegram-token")},
    )

    assert summary["configured"] is True
    assert app.resolve_field("telegram-ops", "bot_token") == "telegram-secret"
    assert "telegram-secret" not in repr(summary)


def test_legacy_plaintext_is_readable_but_never_ready(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    path = workspace.paths.credential_config().parent / "legacy.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[credential]\nid = "legacy"\nprovider = "openai"\napi_key = "old-secret"\n',
        encoding="utf-8",
    )

    summary = CredentialConfigurationApplication(workspace).show("legacy")

    assert summary["legacy_plaintext"] is True
    assert summary["configured"] is False
    assert "old-secret" not in repr(summary)


def test_credential_setup_requires_explicit_secret_ref_replacement_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    application = CredentialConfigurationApplication(workspace)
    application.configure(
        "openai-main",
        provider="openai",
        fields={"api_key": SecretRef("env", "OPENAI_OLD")},
    )
    monkeypatch.setattr("typer.confirm", lambda *_args, **_kwargs: False)
    output = StringIO()

    assert (
        execute_argv(
            [
                "config",
                "credential",
                "setup",
                "--provider",
                "openai",
                "--credential-id",
                "openai-main",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue().splitlines()[-1])["status"] == "unchanged"
    assert application.show("openai-main")["secret_refs"]["api_key"]["id"] == (
        "OPENAI_OLD"
    )
