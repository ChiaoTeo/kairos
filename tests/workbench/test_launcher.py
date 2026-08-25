from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kairospy.surface.workbench import launcher
from kairospy.surface.workbench.launcher import (
    LaunchSetupDeepLink,
    WorkbenchLaunchRequest,
    WorkbenchWorkspaceError,
    run_workbench,
)


def test_launch_request_rejects_parallel_initial_targets() -> None:
    with pytest.raises(ValueError, match="only one initial target"):
        WorkbenchLaunchRequest(
            initial_section="resources",
            launch_attach="paper-demo",
        )


def test_launcher_owns_textual_construction_and_inline_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = SimpleNamespace(owner=object(), load_error=None)
    created: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []

    class FakeWorkbench:
        def __init__(self, actual_state: object, **kwargs: object) -> None:
            created.append({"state": actual_state, **kwargs})
            self.transcript = SimpleNamespace(path=tmp_path / "session.jsonl")

        def run(self, **kwargs: object) -> int:
            runs.append(kwargs)
            return 7

    monkeypatch.setattr(launcher, "load_workbench_state", lambda *args, **kwargs: state)
    monkeypatch.setattr(launcher, "KairosWorkbenchApp", FakeWorkbench)
    monkeypatch.setenv("KAIROS_TEXTUAL_DEV", "1")

    result = run_workbench(
        WorkbenchLaunchRequest(
            workspace=tmp_path,
            launch_setup=LaunchSetupDeepLink("paper-demo", tmp_path / "launch.toml"),
            dry_run=True,
            no_exec=True,
            inline=True,
            transcript_path=tmp_path / "requested.jsonl",
            require_workspace=True,
        )
    )

    assert created == [
        {
            "state": state,
            "initial_section": None,
            "initial_launch_attach": None,
            "initial_launch_setup": ("paper-demo", tmp_path / "launch.toml"),
            "observe_refresh_seconds": 2.0,
            "watch_css": True,
            "transcript_path": tmp_path / "requested.jsonl",
        }
    ]
    assert runs == [{"inline": True, "inline_no_clear": True}]
    assert result.exit_code == 7
    assert result.transcript_path == tmp_path / "session.jsonl"


def test_launcher_can_require_a_resolved_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        launcher,
        "load_workbench_state",
        lambda *args, **kwargs: SimpleNamespace(
            owner=None, load_error="workspace not found"
        ),
    )

    with pytest.raises(WorkbenchWorkspaceError, match="workspace not found"):
        run_workbench(WorkbenchLaunchRequest(require_workspace=True))
