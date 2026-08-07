from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairospy.application.launch.application import LaunchConfigError, LaunchConfigurationApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.commands.launch import _launch_config_path
from io import StringIO


def _write_config(path: Path, *, mode: str = "paper") -> Path:
    content = (
        '[launch]\n'
        'id = "demo-launch"\n'
        f'mode = "{mode}"\n'
        'strategy = "strategy:Factory"\n\n'
        '[account]\n'
        'ref = "paper-account"\n\n'
        f'[{mode}]\n'
    )
    if mode == "live":
        content += "\n[live.safety]\ntrading_enabled = false\n"
    path.write_text(content, encoding="utf-8")
    return path


def test_launch_config_validates_and_explains_toml(tmp_path: Path) -> None:
    config = _write_config(tmp_path / "demo.toml")
    application = LaunchConfigurationApplication()

    result = application.validate(config)
    explanation = application.explain(config)

    assert result["valid"] is True
    assert explanation["launch"]["id"] == "demo-launch"
    assert explanation["account_refs"] == ["paper-account"]


def test_launch_environment_writes_normalized_config_inside_instance(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="launch")
    config = _write_config(tmp_path / "demo.toml")

    environment = LaunchConfigurationApplication().environment(
        config, workspace_root=workspace.paths.root, instance_id="one"
    )

    expected = workspace.paths.root / "launches" / "paper" / "demo-launch" / "instances" / "one"
    assert environment.instance_directory == expected
    normalized = json.loads(environment.normalized_config_path.read_text(encoding="utf-8"))
    assert normalized["launch"]["mode"] == "paper"
    assert environment.process_environment["KAIROS_LAUNCH_INSTANCE_ID"] == "one"
    assert environment.process_environment["KAIROS_LAUNCH_NORMALIZED_CONFIG"] == str(environment.normalized_config_path)
    assert environment.process_environment["KAIROS_EXECUTION_DRY_RUN"] == "true"


def test_live_requires_live_table(tmp_path: Path) -> None:
    config = _write_config(tmp_path / "demo.toml", mode="live")
    config.write_text("[launch]\nid = 'demo-launch'\nmode = 'live'\nstrategy = 'strategy:Factory'\n\n[account]\nref = 'live-account'\n", encoding="utf-8")

    with pytest.raises(LaunchConfigError, match="live.*table is required"):
        LaunchConfigurationApplication().environment(config, workspace_root=tmp_path)


def test_backtest_requires_market_window(tmp_path: Path) -> None:
    config = tmp_path / "backtest.toml"
    config.write_text(
        '[launch]\nid = "backtest"\nmode = "backtest"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "simulated"\n\n[backtest]\n',
        encoding="utf-8",
    )

    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is False
    assert "backtest.market" in " ".join(report["issues"])


def test_mode_plan_resolves_backtest_paths_and_defaults_execution(tmp_path: Path) -> None:
    config = tmp_path / "backtest.toml"
    config.write_text(
        '[launch]\nid = "backtest"\nmode = "backtest"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "simulated"\n\n'
        '[backtest]\ndata_root = "data"\nstorage_format = "jsonl"\n\n'
        '[backtest.market]\nstart = "2024-01-01T00:00:00Z"\nend = "2024-01-02T00:00:00Z"\n',
        encoding="utf-8",
    )

    plan = LaunchConfigurationApplication().plan(config)
    assert plan.backtest_data_root == (tmp_path / "data").resolve()
    assert plan.backtest_storage_format == "jsonl"
    assert plan.execution["dry_run"] is True


def test_backtest_plan_resolves_instance_replay_source(tmp_path: Path) -> None:
    config = tmp_path / "backtest.toml"
    config.write_text(
        """[launch]
id = "backtest"
mode = "backtest"
strategy = "strategy:Factory"

[account]
ref = "simulated"

[backtest]
data_root = "data"

[backtest.market]
start = "2024-01-01T00:00:00Z"
end = "2024-01-02T00:00:00Z"
events = "data/events.jsonl"
""",
        encoding="utf-8",
    )
    plan = LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan()
    assert plan.backtest_replay_file == (tmp_path / "data/events.jsonl").resolve()


def test_live_market_scope_defaults_shared_and_can_be_instance_local(tmp_path: Path) -> None:
    config = tmp_path / "live.toml"
    config.write_text(
        '[launch]\nid = "live"\nmode = "live"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "live-account"\n\n'
        '[live.market]\nscope = "instance"\n\n'
        '[live.safety]\ntrading_enabled = false\n',
        encoding="utf-8",
    )
    plan = LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan()
    assert plan.market_scope == "instance"

    config.write_text(config.read_text(encoding="utf-8").replace('scope = "instance"\n', ""), encoding="utf-8")
    assert LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan().market_scope == "shared"


def test_replay_market_cannot_use_shared_scope(tmp_path: Path) -> None:
    config = tmp_path / "replay.toml"
    config.write_text(
        '[launch]\nid = "replay"\nmode = "paper"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "paper-account"\n\n'
        '[paper]\nevents = "events.jsonl"\n\n'
        '[paper.market]\nscope = "shared"\n',
        encoding="utf-8",
    )
    report = LaunchConfigurationApplication().validate(config, workspace_root=tmp_path)
    assert report["valid"] is False
    assert "paper.market.scope must be instance" in " ".join(report["issues"])


def test_launch_diagnose_reads_workspace_launch_toml(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="launch")
    config_dir = workspace.paths.config / "launches"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_config(config_dir / "demo-launch.toml")
    output = StringIO()

    assert execute_argv(
        ["launch", "diagnose", "validate", "demo-launch", "--workspace", str(workspace.paths.root)],
        output,
    ) == 0
    assert '"valid": true' in output.getvalue()


def test_launch_id_resolves_workspace_owned_config_without_explicit_path(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="launch")
    config = workspace.paths.launch_config("demo-launch")
    _write_config(config)

    assert _launch_config_path(workspace, "demo-launch") == config
