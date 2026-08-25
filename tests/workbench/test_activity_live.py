from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from kairospy.surface.workbench.screens.activity import (
    ActivityKind,
    ActivityOutcome,
    ActivityRecord,
)
from kairospy.surface.workbench.screens.live import LiveBuffer
from kairospy.surface.workbench.widgets import ActivityStream


class _ActivityApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ActivityStream(id="activities")


def test_activity_stream_retains_typed_terminal_records() -> None:
    async def run() -> tuple[tuple[ActivityRecord, ...], str]:
        app = _ActivityApp()
        async with app.run_test() as pilot:
            stream = app.query_one(ActivityStream)
            activity = ActivityRecord(
                activity_id="operation-1",
                kind=ActivityKind.OPERATION,
                outcome=ActivityOutcome.SUCCESS,
                title="下载历史行情 · AAPL",
                copy_text="保存位置 market-history/AAPL.jsonl",
                audit_summary="download AAPL history",
                artifact_path=Path("market-history/AAPL.jsonl"),
            )
            stream.append_activity(activity)
            await pilot.pause()
            return stream.activities, stream.export_plain_text()

    activities, exported = asyncio.run(run())

    assert len(activities) == 1
    assert activities[0].activity_id == "operation-1"
    assert "下载历史行情 · AAPL" in exported
    assert "market-history/AAPL.jsonl" in exported


def test_clear_visible_activity_does_not_need_a_persistent_owner() -> None:
    async def run() -> tuple[tuple[ActivityRecord, ...], str]:
        app = _ActivityApp()
        async with app.run_test() as pilot:
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="notice-1",
                    kind=ActivityKind.SYSTEM,
                    outcome=ActivityOutcome.NOTICE,
                    title="Workspace ready",
                )
            )
            stream.clear_visible_history()
            await pilot.pause()
            return stream.activities, stream.export_plain_text()

    activities, exported = asyncio.run(run())

    assert activities == ()
    assert exported == ""


def test_activity_stream_follows_bottom_until_user_browses_history() -> None:
    async def run() -> tuple[float, int, float, float, float]:
        app = _ActivityApp()
        async with app.run_test(size=(50, 10)) as pilot:
            stream = app.query_one(ActivityStream)
            for index in range(30):
                stream.append_activity(
                    ActivityRecord(
                        activity_id=f"operation-{index}",
                        kind=ActivityKind.OPERATION,
                        outcome=ActivityOutcome.SUCCESS,
                        title=f"line {index}",
                    )
                )
            await pilot.pause()
            followed_y = stream.scroll_y
            initial_end = stream.max_scroll_y

            stream.pause_follow()
            stream.scroll_page_up(animate=False)
            await pilot.pause()
            browsing_y = stream.scroll_y
            stream.append_activity(
                ActivityRecord(
                    activity_id="operation-new",
                    kind=ActivityKind.OPERATION,
                    outcome=ActivityOutcome.SUCCESS,
                    title="new line while browsing",
                )
            )
            await pilot.pause()
            preserved_y = stream.scroll_y

            stream.resume_follow()
            await pilot.pause()
            return (
                followed_y,
                initial_end,
                browsing_y,
                preserved_y,
                stream.scroll_y,
            )

    followed_y, initial_end, browsing_y, preserved_y, resumed_y = asyncio.run(run())

    assert followed_y == initial_end
    assert browsing_y < initial_end
    assert preserved_y == browsing_y
    assert resumed_y > initial_end


def test_live_buffer_is_bounded_and_tracks_hidden_lines() -> None:
    buffer = LiveBuffer("launch/demo", capacity=3)
    buffer.extend(("one", "two", "three"))
    buffer.pause()
    buffer.extend(("four", "five"))

    assert tuple(buffer.lines) == ("three", "four", "five")
    assert buffer.dropped_lines == 2
    assert buffer.unseen_lines == 2
    assert buffer.copy_text() == "three\nfour\nfive"

    buffer.resume()
    assert buffer.following
    assert buffer.unseen_lines == 0


def test_live_buffer_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError, match="capacity must be positive"):
        LiveBuffer("invalid", capacity=0)
