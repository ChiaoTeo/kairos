from __future__ import annotations

from pathlib import Path

from kairospy.application.support.system.application.artifacts import LaunchOutput
from kairospy.infrastructure.persistence.application.artifacts import LaunchInstanceStore


def launch_output(
    launch_directory: str | Path,
    *,
    launch_id: str | None = None,
    mode: str | None = None,
    write_legacy_jsonl: bool = False,
) -> LaunchOutput:
    return LaunchOutput(
        launch_id=launch_id,
        mode=mode,
        write_legacy_jsonl=write_legacy_jsonl,
        launch_directory=launch_directory,
        store=LaunchInstanceStore(launch_directory),
    )


__all__ = ["launch_output"]
