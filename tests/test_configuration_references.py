from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from kairospy.application.account import AccountConfigurationApplication
from kairospy.application.config import ConfigurationReferenceApplication
from kairospy.application.credential import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.launch.application import LaunchConfigurationApplication
from kairospy.application.notification import (
    NotificationAdminApplication,
    NotificationSecretRef,
)
from kairospy.application.reference import ReferenceProviderConfigurationApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _draft(workspace, launch_id: str, values: dict[str, object]) -> Path:
    LaunchConfigurationApplication().save_draft(workspace.paths.root, launch_id, values)
    return LaunchConfigurationApplication().draft_path(workspace.paths.root, launch_id)


def test_reference_query_includes_published_and_draft_launches(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="r")
    published = workspace.paths.launch_config("published")
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_text(
        '[launch]\nid = "published"\nmode = "paper"\nstrategy = "x:y"\n'
        '[accounts.main]\nref = "paper-main"\ntrade = false\n'
        '[agent]\nenabled = true\ncredential = "openai-main"\nmodel = "gpt-test"\n'
        '[notifications.routes]\nsignals = ["telegram-ops"]\n',
        encoding="utf-8",
    )
    draft = _draft(
        workspace,
        "working",
        {
            "launch": {"id": "working", "mode": "paper", "strategy": "x:y"},
            "accounts": {"main": {"ref": "paper-main", "trade": False}},
            "agent": {
                "enabled": True,
                "credential": "openai-main",
                "model": "gpt-test",
            },
            "notifications": {"routes": {"signals": ["telegram-ops"]}},
        },
    )

    references = ConfigurationReferenceApplication(workspace)

    assert {item["source"] for item in references.account_references("paper-main")} == {
        str(published.relative_to(workspace.paths.root)),
        str(draft.relative_to(workspace.paths.root)),
    }
    assert len(references.credential_references("openai-main")) == 2
    assert len(references.destination_references("telegram-ops")) == 2


def test_credential_delete_requires_reference_override_and_removes_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="r")
    monkeypatch.setenv("OPENAI_DELETE_TEST", "not-persisted")
    CredentialConfigurationApplication(workspace).configure(
        "openai-main",
        provider="openai",
        fields={"api_key": SecretRef("env", "OPENAI_DELETE_TEST")},
    )
    evidence = workspace.paths.child(
        "state", "configuration", "models", "openai-main.json"
    )
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("{}", encoding="utf-8")
    _draft(
        workspace,
        "working",
        {
            "launch": {"id": "working", "mode": "paper", "strategy": "x:y"},
            "agent": {
                "enabled": True,
                "credential": "openai-main",
                "model": "gpt-test",
            },
        },
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "config",
                "credential",
                "delete",
                "openai-main",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )
    assert "referenced" in output.getvalue()
    assert CredentialConfigurationApplication(workspace).show("openai-main")

    output = StringIO()
    assert (
        execute_argv(
            [
                "config",
                "credential",
                "delete",
                "openai-main",
                "--force",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["forced"] is True
    assert not evidence.exists()


def test_account_and_destination_delete_protect_draft_references(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="r")
    AccountConfigurationApplication(workspace).simulate("paper-main")
    monkeypatch.setenv(
        "TELEGRAM_DELETE_TEST", "123456789:abcdefghijklmnopqrstuvwxyzABCDE"
    )
    NotificationAdminApplication(workspace).configure(
        "telegram-ops",
        provider="telegram",
        secret_ref=NotificationSecretRef("env", "TELEGRAM_DELETE_TEST"),
        chat_id="-10042",
    )
    _draft(
        workspace,
        "working",
        {
            "launch": {"id": "working", "mode": "paper", "strategy": "x:y"},
            "accounts": {"main": {"ref": "paper-main", "trade": False}},
            "notifications": {"routes": {"signals": ["telegram-ops"]}},
        },
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "account",
                "--workspace",
                str(workspace.paths.root),
                "remove",
                "--account-id",
                "paper-main",
            ],
            output,
        )
        != 0
    )
    assert "referenced" in output.getvalue()
    assert AccountConfigurationApplication(workspace).show("paper-main")

    output = StringIO()
    assert (
        execute_argv(
            [
                "notifications",
                "delete",
                "telegram-ops",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )
    assert ".drafts/working.toml" in output.getvalue()
    assert NotificationAdminApplication(workspace).show("telegram-ops")


def test_data_connection_delete_protects_draft_references(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="r")
    monkeypatch.setenv("MASSIVE_DELETE_TEST", "not-persisted")
    CredentialConfigurationApplication(workspace).configure(
        "massive-readonly",
        provider="massive",
        fields={"api_key": SecretRef("env", "MASSIVE_DELETE_TEST")},
    )
    ReferenceProviderConfigurationApplication(workspace).configure_massive(
        credential_id="massive-readonly"
    )
    _draft(
        workspace,
        "working",
        {
            "launch": {"id": "working", "mode": "paper", "strategy": "x:y"},
            "execution": {"enabled": False},
            "paper": {"market": {"profile": "massive", "scope": "shared"}},
        },
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "config",
                "data",
                "delete",
                "massive",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )
    assert ".drafts/working.toml" in output.getvalue()
    assert ReferenceProviderConfigurationApplication(workspace).show("massive")
