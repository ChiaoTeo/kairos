from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.screens.command_line import CommandLineScreen
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
    assert not any(
        event["event"] == "output"
        and str(event["text"]).startswith("Kairos Workbench\nWorkspace:")
        for event in events
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


def test_guided_arguments_produce_one_semantic_action(tmp_path: Path) -> None:
    async def run() -> tuple[dict[str, object], ...]:
        app = KairosWorkbenchApp(
            _state(tmp_path), transcript_path=tmp_path / "guided.jsonl"
        )
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = lambda query: ()  # type: ignore[method-assign]
            screen.submit("1")
            screen.submit("1")
            screen.submit("AAPL")
            await pilot.pause(0.1)
            return app.transcript.events

    events = asyncio.run(run())
    actions = tuple(event for event in events if event["event"] == "action")

    assert len(actions) == 1
    assert actions[0]["action"] == "market.find"
    assert actions[0]["display"] == ("首页 / 市场行情 › 搜索标的并查看行情 · AAPL")


def test_complete_operation_redacts_paired_secret_arguments(tmp_path: Path) -> None:
    async def run() -> tuple[str, dict[str, object]]:
        app = KairosWorkbenchApp(
            _state(tmp_path), transcript_path=tmp_path / "secret-action.jsonl"
        )
        async with app.run_test(size=(80, 24)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._record_action(
                "integration.connect",
                ("--token", "top-secret-value"),
                equivalent_command=(
                    "kairos",
                    "integration",
                    "connect",
                    "--token",
                    "top-secret-value",
                ),
            )
            screen._emit_operation("连接 Provider")
            action = next(
                event for event in app.transcript.events if event["event"] == "action"
            )
            return screen._output().plain_text, action

    output, action = asyncio.run(run())
    encoded = json.dumps(action, ensure_ascii=False)
    assert "top-secret-value" not in output
    assert "top-secret-value" not in encoded
    assert action["arguments"] == ["--token", "<redacted>"]
    assert "<redacted>" in output


def test_scope_and_confirmation_do_not_duplicate_complete_operation(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[str, tuple[dict[str, object], ...]]:
        app = KairosWorkbenchApp(
            _state(tmp_path), transcript_path=tmp_path / "confirmed-action.jsonl"
        )
        async with app.run_test(size=(80, 24)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._record_action("market.download", ("AAPL",))
            screen._write("下载范围：AAPL")
            screen.request_confirmation("确认下载 AAPL", lambda: None)
            return screen._output().plain_text, app.transcript.events

    output, events = asyncio.run(run())
    actions = tuple(event for event in events if event["event"] == "action")
    assert len(actions) == 1
    assert output.count("market.download · AAPL") == 1


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
