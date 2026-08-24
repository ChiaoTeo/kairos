from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairospy.system.apps.workspace.application import (
    WorkspaceApplication,
    WorkspaceConfigurationTransaction,
    recover_configuration_transactions,
)


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="tx")


def test_configuration_transaction_commits_multiple_private_files(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    first = workspace.paths.child("config", "credentials", "model.toml")
    second = workspace.paths.child("config", "model-connections", "model.toml")
    transaction = WorkspaceConfigurationTransaction(workspace, "model connection")
    transaction.stage_text(first, 'ref = "secrets/model/version/api_key"\n')
    transaction.stage_text(second, 'credential_id = "model"\n')

    transaction.commit()

    assert first.read_text(encoding="utf-8").startswith("ref")
    assert second.read_text(encoding="utf-8").startswith("credential_id")
    assert first.stat().st_mode & 0o777 == 0o600
    assert recover_configuration_transactions(workspace) == ()


def test_configuration_transaction_rolls_back_every_target_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    first = workspace.paths.child("config", "a.toml")
    second = workspace.paths.child("config", "b.toml")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("old-a\n", encoding="utf-8")
    second.write_text("old-b\n", encoding="utf-8")
    transaction = WorkspaceConfigurationTransaction(workspace, "failure")
    transaction.stage_text(first, "new-a\n")
    transaction.stage_text(second, "new-b\n")
    from kairospy.system.apps.configuration.services import transactions as module

    original = module._replace_file
    failed = False

    def replace(source: Path, target: Path) -> None:
        nonlocal failed
        if target == second and source.parent.name == "staged" and not failed:
            failed = True
            raise OSError("simulated replacement failure")
        original(source, target)

    monkeypatch.setattr(module, "_replace_file", replace)

    with pytest.raises(OSError, match="simulated"):
        transaction.commit()

    assert first.read_text(encoding="utf-8") == "old-a\n"
    assert second.read_text(encoding="utf-8") == "old-b\n"


def test_recovery_rolls_back_interrupted_transaction_and_staged_secret(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.paths.child("config", "model.toml")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("new\n", encoding="utf-8")
    secret_version = workspace.paths.child("secrets", "model", "new-version")
    secret_version.mkdir(parents=True)
    (secret_version / "api_key").write_text("not-in-journal", encoding="utf-8")
    root = workspace.paths.child(
        "state", "configuration", "transactions", "interrupted"
    )
    (root / "backup").mkdir(parents=True)
    (root / "backup" / "0").write_text("old\n", encoding="utf-8")
    journal = {
        "version": 1,
        "transaction_id": "interrupted",
        "phase": "applying",
        "entries": [
            {
                "target": "config/model.toml",
                "staged": "staged/0",
                "backup": "backup/0",
                "existed": True,
                "mode": 0o600,
            }
        ],
        "cleanup_on_rollback": ["secrets/model/new-version"],
        "cleanup_on_commit": [],
    }
    (root / "journal.json").write_text(json.dumps(journal), encoding="utf-8")

    assert recover_configuration_transactions(workspace) == ("interrupted",)
    assert target.read_text(encoding="utf-8") == "old\n"
    assert secret_version.exists() is False


def test_transaction_journal_never_contains_staged_content_or_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.paths.child("config", "secret-ref.toml")
    transaction = WorkspaceConfigurationTransaction(
        workspace, "label-must-not-be-persisted"
    )
    transaction.stage_text(target, 'value = "plaintext-must-not-enter-journal"\n')
    from kairospy.system.apps.configuration.services import transactions as module

    journals: list[str] = []
    original = module._write_journal

    def capture(root: Path, value: dict[str, object]) -> None:
        journals.append(json.dumps(value))
        original(root, value)

    monkeypatch.setattr(module, "_write_journal", capture)
    transaction.commit()

    encoded = "".join(journals)
    assert "plaintext-must-not-enter-journal" not in encoded
    assert "label-must-not-be-persisted" not in encoded


def test_transaction_preparation_failure_cleans_unreferenced_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    secret = workspace.paths.child("secrets", "draft", "version")
    secret.mkdir(parents=True)
    (secret / "api_key").write_text("staged", encoding="utf-8")
    transaction = WorkspaceConfigurationTransaction(workspace, "prepare failure")
    transaction.stage_text(workspace.paths.child("config", "a.toml"), "a = 1\n")
    transaction.stage_text(workspace.paths.child("config", "b.toml"), "b = 2\n")
    transaction.cleanup_on_rollback(secret)
    from kairospy.system.apps.configuration.services import transactions as module

    original = module._write_private
    staged_writes = 0

    def fail(path: Path, content: bytes, *, mode: int) -> None:
        nonlocal staged_writes
        if path.parent.name == "staged":
            staged_writes += 1
            if staged_writes == 2:
                raise OSError("simulated preparation failure")
        original(path, content, mode=mode)

    monkeypatch.setattr(module, "_write_private", fail)

    with pytest.raises(OSError, match="preparation"):
        transaction.commit()
    assert secret.exists() is False
    assert list(
        workspace.paths.child("state", "configuration", "transactions").glob("*")
    ) == []
