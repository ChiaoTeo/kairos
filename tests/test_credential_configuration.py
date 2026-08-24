from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
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


def test_direct_secret_values_switch_atomically_to_private_workspace_files(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    application = CredentialConfigurationApplication(workspace)

    summary = application.configure_secret_values(
        "okx-trade",
        provider="okx",
        role="trade",
        values={
            "api_key": "key-one",
            "api_secret": "secret-one",
            "passphrase": "phrase-one",
        },
    )

    config_path = workspace.paths.credential_config().parent / "okx-trade.toml"
    config_text = config_path.read_text(encoding="utf-8")
    assert "key-one" not in config_text
    assert "secret-one" not in config_text
    assert "phrase-one" not in config_text
    assert summary["secret_storage"] == "workspace-private-files"
    for field, expected in {
        "api_key": "key-one",
        "api_secret": "secret-one",
        "passphrase": "phrase-one",
    }.items():
        assert application.resolve_field("okx-trade", field) == expected
        reference = summary["secret_refs"][field]
        secret_path = workspace.paths.root / reference["id"]
        assert secret_path.stat().st_mode & 0o777 == 0o600


def test_replacing_private_values_removes_retired_version_only_after_switch(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    application = CredentialConfigurationApplication(workspace)
    first = application.configure_secret_values(
        "openai-main", provider="openai", values={"api_key": "first"}
    )
    old_path = workspace.paths.root / first["secret_refs"]["api_key"]["id"]

    second = application.configure_secret_values(
        "openai-main",
        provider="openai",
        values={"api_key": "second"},
        overwrite=True,
    )

    new_path = workspace.paths.root / second["secret_refs"]["api_key"]["id"]
    assert old_path.exists() is False
    assert new_path.read_text(encoding="utf-8").strip() == "second"
    assert application.resolve_field("openai-main", "api_key") == "second"


def test_deleting_credential_removes_managed_private_values(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    application = CredentialConfigurationApplication(workspace)
    application.configure_secret_values(
        "telegram-main", provider="telegram", values={"bot_token": "token"}
    )
    secret_root = workspace.paths.root / "secrets" / "telegram-main"

    application.delete("telegram-main")

    assert secret_root.exists() is False


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
