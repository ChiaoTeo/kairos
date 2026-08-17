from pathlib import Path

import pytest

from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.config import ConfigApplication


def test_workspace_init_creates_manifest_and_runtime_layout(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")

    assert workspace.workspace_id == "demo"
    assert 'workspace_id = "demo"' in workspace.paths.manifest.read_text()
    assert workspace.cli_format == "json"
    assert workspace.paths.run.is_dir()
    logical_reference_socket = (
        workspace.paths.root / "run" / "reference" / "control.sock"
    )
    if len(str(logical_reference_socket).encode()) <= 100:
        assert workspace.paths.reference_socket() == logical_reference_socket
    else:
        assert workspace.paths.reference_socket().parent == Path("/tmp")
    assert (
        workspace.paths.account_config()
        == workspace.paths.root / "config" / "accounts" / "accounts.toml"
    )
    assert (
        workspace.paths.account_state()
        == workspace.paths.root / "state" / "account" / "account-state.json"
    )
    instance = workspace.instance("paper", "demo", "run-001")
    assert instance.socket("account") == instance.paths.process_socket("account")
    assert (
        instance.state("execution", "execution-state.json")
        == instance.root / "state" / "execution" / "execution-state.json"
    )
    assert (
        instance.state("risk", "risk-state.json")
        == instance.root / "state" / "risk" / "risk-state.json"
    )
    assert (
        workspace.paths.account_leases()
        == workspace.paths.root / "state" / "account-locks"
    )
    assert workspace.paths.market_connections_root().is_dir()
    assert workspace.paths.reference_database() == (
        workspace.paths.root / "state" / "reference" / "reference.sqlite"
    )
    assert workspace.paths.orders_root() == (
        workspace.paths.root / "state" / "execution" / "orders"
    )
    assert not (workspace.paths.root / "accounts").exists()
    assert not (workspace.paths.root / "credentials").exists()
    assert not (workspace.paths.root / "reference").exists()
    assert not (workspace.paths.root / "orders").exists()


def test_project_init_creates_dot_kairos_resource_layout(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project"
    )

    assert workspace.workspace_id == "project"
    assert workspace.paths.root == (tmp_path / "project" / ".kairos").resolve()
    assert workspace.paths.manifest.name == "kairos.toml"
    assert (
        workspace.paths.account_config()
        == workspace.paths.root / "config" / "accounts" / "accounts.toml"
    )
    assert (
        workspace.paths.account_leases()
        == workspace.paths.root / "state" / "account-locks"
    )
    assert workspace.paths.orders_root().is_dir()
    assert workspace.paths.project_root == (tmp_path / "project").resolve()
    assert (
        WorkspaceApplication().open(workspace.paths.root).paths.root
        == workspace.paths.root
    )


def test_project_init_backtest_template_creates_complete_offline_starter(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    workspace = WorkspaceApplication().init_project(
        project, workspace_id="project", template="backtest"
    )

    assert (project / "kairos_demo" / "strategy.py").is_file()
    assert (project / "KAIROS_QUICKSTART.md").is_file()
    assert (workspace.paths.account_config().parent / "demo-paper.toml").is_file()
    events = workspace.paths.data_root() / "examples" / "demo-market.jsonl"
    assert len(events.read_text(encoding="utf-8").splitlines()) == 5
    launch = workspace.paths.launch_config("demo-backtest")
    assert launch.is_file()

    from kairospy.application.launch.application import LaunchConfigurationApplication

    report = LaunchConfigurationApplication().validate(
        launch, workspace_root=workspace.paths.root
    )
    assert report["valid"] is True

    from kairospy.application.strategy.services.loader import load_strategy

    entrypoint = load_strategy(
        "kairos_demo.strategy:DemoBacktest", root=project, params={}
    )
    assert entrypoint.strategy.strategy_id == "demo-backtest"
    strategy_source = (project / "kairos_demo" / "strategy.py").read_text(
        encoding="utf-8"
    )
    assert "def on_bar(" in strategy_source
    assert "def on_market(" not in strategy_source
    assert "kairos project doctor" in (project / "KAIROS_QUICKSTART.md").read_text(
        encoding="utf-8"
    )

    diagnosis = ConfigApplication(workspace).doctor()
    assert diagnosis["ok"] is True
    assert diagnosis["ready"] is True
    assert diagnosis["launches"] == [
        {
            "launch_id": "demo-backtest",
            "path": str(launch),
            "valid": True,
            "issues": [],
            "ready": True,
            "readiness_issues": [],
        }
    ]
    assert diagnosis["next_steps"] == ["kairos launch start demo-backtest"]


def test_project_doctor_explains_how_to_make_an_empty_project_runnable(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project"
    )

    diagnosis = ConfigApplication(workspace).doctor()

    assert diagnosis["ok"] is True
    assert diagnosis["ready"] is False
    assert diagnosis["next_steps"] == ["kairos project scaffold --template backtest"]

    created = WorkspaceApplication().install_template(workspace, template="backtest")
    assert created
    assert ConfigApplication(workspace).doctor()["ready"] is True


def test_project_doctor_detects_missing_starter_resources(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project", template="backtest"
    )
    replay = workspace.paths.data_root() / "examples" / "demo-market.jsonl"
    replay.unlink()

    diagnosis = ConfigApplication(workspace).doctor()

    assert diagnosis["ok"] is False
    assert diagnosis["ready"] is False
    assert diagnosis["launches"][0]["valid"] is True
    assert diagnosis["launches"][0]["ready"] is False
    assert str(replay) in diagnosis["launches"][0]["readiness_issues"][0]
    assert "restore the missing launch resources" in diagnosis["next_steps"][0]


def test_project_init_template_rejects_unknown_name_before_writing(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"

    with pytest.raises(ValueError, match="unknown project template"):
        WorkspaceApplication().init_project(project, template="unknown")

    assert not project.exists()


def test_project_init_template_does_not_overwrite_project_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "kairos_demo"
    package.mkdir(parents=True)
    strategy = package / "strategy.py"
    strategy.write_text("user content", encoding="utf-8")

    with pytest.raises(FileExistsError, match="would overwrite"):
        WorkspaceApplication().init_project(project, template="backtest")

    assert strategy.read_text(encoding="utf-8") == "user content"
    assert not (project / ".kairos").exists()


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


def test_workspace_accepts_table_as_cli_format(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    workspace.paths.manifest.write_text(
        'version = 1\nworkspace_id = "demo"\n\n[cli]\nformat = "table"\n',
        encoding="utf-8",
    )

    assert WorkspaceApplication().open(workspace.paths.root).cli_format == "table"


def test_workspace_resolves_market_connection_from_manifest(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    workspace.paths.manifest.write_text(
        'version = 1\nworkspace_id = "demo"\n\n'
        "[market.connections.massive-equity]\n"
        'provider = "massive-rest"\n'
        'credential_id = "massive-readonly"\n',
        encoding="utf-8",
    )

    assert WorkspaceApplication().market_connection(workspace, "massive-equity") == {
        "provider": "massive-rest",
        "credential_id": "massive-readonly",
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


def test_workspace_child_rejects_path_components_that_escape_root(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo")
    with pytest.raises(ValueError):
        workspace.paths.child("..", "outside")


def test_instance_workspace_scopes_runtime_resources(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo", workspace_id="demo")
    instance = workspace.instance("paper", "btc-sma", "run-001")
    instance.prepare()

    assert (
        instance.root
        == workspace.paths.root
        / "launches"
        / "paper"
        / "btc-sma"
        / "instances"
        / "run-001"
    )
    assert instance.socket("market") == instance.paths.process_socket("market")
    assert (
        instance.snapshot("market.snapshot")
        == instance.root / "snapshots" / "market.snapshot"
    )
    assert (
        instance.state("account", "account-state.json")
        == instance.root / "state" / "account" / "account-state.json"
    )
    assert instance.root.is_dir()
    assert instance.paths.root == instance.root
    assert instance.component_manifest() == instance.root / "manifest.json"
    assert instance.normalized_config() == instance.root / "config" / "normalized.json"
    assert instance.lifecycle_journal() == (
        instance.root / "state" / "launch" / "lifecycle.jsonl"
    )
    for legacy_directory in ("sockets", "health", "locks", "checkpoints"):
        assert not (instance.root / legacy_directory).exists()
    assert (
        instance.market_state("cursor.json")
        == instance.root / "state" / "market" / "cursor.json"
    )


def test_instance_workspace_rejects_path_components(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "demo")
    with pytest.raises(ValueError):
        workspace.instance("paper", "../launch", "run")


def test_instance_socket_uses_stable_alias_when_workspace_path_is_too_long(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / ("workspace-" + "x" * 70), workspace_id="long"
    )
    socket = workspace.instance(
        "paper", "aapl-paper", "0df2adc3-b650-4a93-aa47-e3f12fc7cd69"
    ).socket("market")

    assert socket.parent == Path("/tmp")
    assert socket.name.startswith("kairos-process-")
    assert socket.name.endswith("-market.sock")
