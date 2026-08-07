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
    instance = workspace.instance("paper", "demo", "run-001")
    assert instance.socket("account") == workspace.paths.instance_socket("paper", "demo", "run-001", "account")
    assert instance.state("execution", "execution-state.json") == instance.root / "state" / "execution" / "execution-state.json"
    assert instance.state("risk", "risk-state.json") == instance.root / "state" / "risk" / "risk-state.json"
    assert workspace.paths.account_leases() == workspace.paths.root / "state" / "account-locks"
    assert workspace.paths.market_connections_root().is_dir()


def test_project_init_creates_dot_kairos_resource_layout(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(tmp_path / "project", workspace_id="project")

    assert workspace.workspace_id == "project"
    assert workspace.paths.root == (tmp_path / "project" / ".kairos").resolve()
    assert workspace.paths.manifest.name == "kairos.toml"
    assert workspace.paths.account_config() == workspace.paths.root / "accounts" / "accounts.toml"
    assert workspace.paths.account_leases() == workspace.paths.root / "state" / "account-locks"
    assert workspace.paths.orders_root().is_dir()
    assert workspace.paths.project_root == (tmp_path / "project").resolve()
    assert WorkspaceApplication().open(workspace.paths.root).paths.root == workspace.paths.root


def test_legacy_workspace_uses_itself_as_strategy_project_root(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")

    assert workspace.paths.project_root == workspace.paths.root


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


def test_workspace_resolves_market_connection_from_manifest(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    workspace.paths.manifest.write_text(
        'version = 1\nworkspace_id = "demo"\n\n'
        '[market.connections.binance-equity]\n'
        'provider = "binance-equity-rest"\n'
        'credential_id = "binance-equity-readonly"\n',
        encoding="utf-8",
    )

    assert WorkspaceApplication().market_connection(workspace, "binance-equity") == {
        "provider": "binance-equity-rest",
        "credential_id": "binance-equity-readonly",
    }


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
    assert instance.socket("market") == workspace.paths.instance_socket("paper", "btc-sma", "run-001", "market")
    assert instance.snapshot("market.snapshot") == instance.root / "snapshots" / "market.snapshot"
    assert instance.state("account", "account-state.json") == instance.root / "state" / "account" / "account-state.json"
    assert instance.root.is_dir()
    assert instance.market_state("cursor.json") == instance.root / "state" / "market" / "cursor.json"


def test_instance_workspace_rejects_path_components(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo")
    with pytest.raises(ValueError):
        workspace.instance("paper", "../launch", "run")


def test_instance_socket_uses_stable_alias_when_workspace_path_is_too_long(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / ("workspace-" + "x" * 70), workspace_id="long")
    socket = workspace.paths.instance_socket("paper", "aapl-paper", "0df2adc3-b650-4a93-aa47-e3f12fc7cd69", "market")

    assert socket.parent == Path("/tmp")
    assert socket.name.startswith("kairos-instance-")
    assert socket.name.endswith("-market.sock")
