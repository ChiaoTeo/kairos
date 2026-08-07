from pathlib import Path

import pytest

from kairospy.application.workspace import WorkspaceApplication


def test_workspace_init_creates_manifest_and_runtime_layout(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")

    assert workspace.workspace_id == "demo"
    assert 'workspace_id = "demo"' in workspace.paths.manifest.read_text()
    assert workspace.cli_format == "json"
    assert workspace.paths.run.is_dir()
    assert workspace.paths.reference_socket().parent == workspace.paths.root / "run" / "reference"
    assert workspace.paths.account_config() == workspace.paths.root / "accounts" / "accounts.toml"
    assert workspace.paths.account_state() == workspace.paths.root / "state" / "account" / "account-state.json"
    assert workspace.paths.account_leases() == workspace.paths.root / "state" / "account-locks"


def test_project_init_creates_dot_kairos_resource_layout(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(tmp_path / "project", workspace_id="project")

    assert workspace.workspace_id == "project"
    assert workspace.paths.root == (tmp_path / "project" / ".kairos").resolve()
    assert workspace.paths.manifest.name == "kairos.toml"
    assert workspace.paths.account_config() == workspace.paths.root / "accounts" / "accounts.toml"
    assert workspace.paths.account_leases() == workspace.paths.root / "state" / "account-locks"
    assert workspace.paths.orders_root().is_dir()


def test_project_init_rejects_legacy_root_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "workspace.toml").write_text(
        'version = 1\nworkspace_id = "legacy"\n', encoding="utf-8"
    )

    with pytest.raises(FileExistsError, match="legacy workspace manifest"):
        WorkspaceApplication().init_project(project, workspace_id="project")


def test_workspace_open_requires_manifest(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="workspace manifest is required"):
        WorkspaceApplication().open(tmp_path)


def test_workspace_cli_format_is_loaded_from_manifest(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    workspace.paths.manifest.write_text(
        'version = 1\nworkspace_id = "demo"\n\n[cli]\nformat = "text"\n',
        encoding="utf-8",
    )

    assert WorkspaceApplication().open(workspace.paths.root).cli_format == "text"


def test_workspace_resolve_discovers_current_ancestor_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    nested = workspace.paths.root / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.delenv("KAIROS_WORKSPACE", raising=False)
    assert WorkspaceApplication().resolve().workspace_id == "demo"
    monkeypatch.setenv("KAIROS_WORKSPACE", str(workspace.paths.root))
    assert WorkspaceApplication().resolve().paths.root == workspace.paths.root


def test_workspace_child_rejects_path_components_that_escape_root(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo")
    with pytest.raises(ValueError):
        workspace.paths.child("..", "outside")


def test_instance_workspace_scopes_runtime_resources(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    instance = workspace.instance("paper", "btc-sma", "run-001")
    instance.prepare()

    assert instance.root == workspace.paths.root / "launches" / "paper" / "btc-sma" / "instances" / "run-001"
    assert instance.socket("market") == instance.root / "sockets" / "market.sock"
    assert instance.snapshot("market.snapshot") == instance.root / "snapshots" / "market.snapshot"
    assert instance.state("account", "account-state.json") == instance.root / "state" / "account" / "account-state.json"
    assert instance.root.is_dir()


def test_instance_workspace_rejects_path_components(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo")
    with pytest.raises(ValueError):
        workspace.instance("paper", "../launch", "run")
