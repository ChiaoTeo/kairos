from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from kairospy.application.support.launch.application.launcher import LaunchTarget, TradingSystemLauncher
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.control import daemon as daemon_module
from kairospy.application.support.launch.application.control.daemon import LaunchDaemonService
from kairospy.application.system.application.resources import TradingSystemResources
from kairospy.application.support.launch.application.launcher import LaunchTargetDescriptor


def test_foreground_launch_propagates_one_instance_id_to_runtime_and_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed: dict[str, str | None] = {}

    class Resolver:
        def resolve(self, mode, config_path, *, strategy_ref=None, launch_directory=None):
            def run() -> object:
                observed["environment"] = os.environ.get("KAIROS_LAUNCH_INSTANCE_ID")
                return SimpleNamespace(
                    runtime=SimpleNamespace(
                        strategy_id="identity-test",
                        event_count=0,
                    ),
                    fills=(),
                    trades=(),
                    initial_equity=None,
                    final_equity=None,
                    net_profit=None,
                    total_return=None,
                    metrics={},
                )

            return LaunchTarget(
                mode=mode,
                launch_id="identity-test-launch",
                launch_directory=Path(launch_directory or tmp_path),
                _runner=run,
                _bind_stop=lambda _stop: None,
            )

    monkeypatch.delenv("KAIROS_LAUNCH_INSTANCE_ID", raising=False)
    result = LaunchDaemonService(
        tmp_path / "launches",
        target_resolver=Resolver(),
    ).launch_foreground(
        mode=RuntimeMode.PAPER,
        config_path=tmp_path / "config.toml",
    )

    assert result.launch_instance_id
    assert observed["environment"] == result.launch_instance_id
    assert "KAIROS_LAUNCH_INSTANCE_ID" not in os.environ

    group = tmp_path / "launches" / "paper" / "identity-test-launch"
    current = json.loads((group / "current.json").read_text())
    state = json.loads((result.state_path).read_text())
    summary = json.loads((result.summary_path).read_text())
    assert current["launch_instance_id"] == result.launch_instance_id
    assert state["launch_instance_id"] == result.launch_instance_id
    assert summary["launch_instance_id"] == result.launch_instance_id


def test_background_launch_passes_the_same_instance_id_to_child_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Resolver:
        def describe(self, mode, config_path):
            return LaunchTargetDescriptor(
                mode=mode,
                launch_id="background-identity-test",
                launch_directory=tmp_path,
            )

    class Process:
        pid = 12345

    def popen(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return Process()

    monkeypatch.delenv("KAIROS_LAUNCH_INSTANCE_ID", raising=False)
    monkeypatch.setattr(daemon_module.subprocess, "Popen", popen)
    result = LaunchDaemonService(
        tmp_path / "launches",
        target_resolver=Resolver(),
    ).start_background(
        mode=RuntimeMode.PAPER,
        config_path=tmp_path / "config.toml",
    )

    assert result.launch_instance_id
    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert child_env["KAIROS_LAUNCH_INSTANCE_ID"] == result.launch_instance_id


def test_launcher_log_records_the_runtime_instance_id(tmp_path: Path, monkeypatch) -> None:
    class Strategy:
        strategy_id = "log-identity-test"

    result = SimpleNamespace(
        runtime=SimpleNamespace(event_count=0),
    )

    class TradingSystem:
        def __init__(self, _spec):
            pass

        def run(self):
            return result

    monkeypatch.setattr(
        "kairospy.application.support.launch.application.launcher.TradingSystem",
        TradingSystem,
    )
    monkeypatch.setenv("KAIROS_LAUNCH_INSTANCE_ID", "log-instance-1")

    TradingSystemLauncher()._launch_configured(
        launch_id="log-launch",
        mode=RuntimeMode.PAPER,
        strategy=Strategy(),
        launch_directory=tmp_path / "instances" / "log-instance-1",
        normalized_config={},
        resources=TradingSystemResources(),
    )

    log = (tmp_path / "instances" / "log-instance-1" / "launch.log").read_text()
    assert "launch_instance_id: log-instance-1" in log
