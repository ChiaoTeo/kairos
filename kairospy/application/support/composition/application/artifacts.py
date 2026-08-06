from __future__ import annotations

from pathlib import Path

from kairospy.application.support.launch.application.artifacts import LaunchOutput
from kairospy.infrastructure.persistence.application.run import open_run_store


def launch_output(
    launch_directory: str | Path,
    *,
    launch_id: str | None = None,
    mode: str | None = None,
) -> LaunchOutput:
    return LaunchOutput(
        launch_id=launch_id,
        mode=mode,
        store=open_run_store(Path(launch_directory) / "run.sqlite"),
    )


__all__ = ["launch_output"]
