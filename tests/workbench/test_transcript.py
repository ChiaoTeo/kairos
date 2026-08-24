from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.transcript import WorkbenchTranscript


def _state(tmp_path: Path) -> WorkbenchState:
    root = tmp_path / ".kairos"
    owner = SimpleNamespace(
        workspace_id="transcript-fixture",
        paths=SimpleNamespace(root=root, project_root=tmp_path),
    )
    return WorkbenchState(owner=owner, workspace_arg=root)


def test_workbench_writes_agent_readable_jsonl(tmp_path: Path) -> None:
    transcript_path = tmp_path / "session.jsonl"

    async def run() -> tuple[dict[str, object], ...]:
        app = KairosWorkbenchApp(_state(tmp_path), transcript_path=transcript_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("slash", "h", "e", "l", "p", "enter")
            await pilot.pause()
            return app.transcript.events

    events = asyncio.run(run())
    persisted = tuple(
        json.loads(line)
        for line in transcript_path.read_text(encoding="utf-8").splitlines()
    )

    assert transcript_path.stat().st_mode & 0o777 == 0o600
    pointer = json.loads(
        (transcript_path.parent / "current.json").read_text(encoding="utf-8")
    )
    assert pointer["transcript"] == str(transcript_path)
    assert [event["event"] for event in events].count("input") == 1
    assert any(
        event["event"] == "output" and "/market [代码]" in str(event["text"])
        for event in events
    )
    assert (
        sum(
            event["event"] == "output"
            and str(event["text"]).startswith("Kairos Workbench\nWorkspace:")
            for event in events
        )
        == 1
    )
    assert persisted == events


def test_observe_records_reproducible_non_interactive_command(tmp_path: Path) -> None:
    async def run() -> tuple[dict[str, object], ...]:
        app = KairosWorkbenchApp(
            _state(tmp_path), transcript_path=tmp_path / "observe.jsonl"
        )
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("o", "b", "s", "e", "r", "v", "e", "enter")
            await pilot.pause()
            return app.transcript.events

    action = next(event for event in asyncio.run(run()) if event["event"] == "action")

    assert action["action"] == "system.observe"
    assert action["equivalent_command"] == (
        f"kairos observe --workspace {tmp_path / '.kairos'} --once"
    )


def test_transcript_redacts_common_secret_assignments(tmp_path: Path) -> None:
    transcript = WorkbenchTranscript.create(
        _state(tmp_path), tmp_path / "redacted.jsonl"
    )

    transcript.record_output(
        screen="ResourceSetupScreen",
        widget="result",
        text="api_key=abcd password: hunter2 --token sample\nstatus=ready",
    )

    output = (tmp_path / "redacted.jsonl").read_text(encoding="utf-8")
    assert "abcd" not in output
    assert "hunter2" not in output
    assert "sample" not in output
    assert "api_key=<redacted>" in output
    assert "password=<redacted>" in output
    assert "status=ready" in output
