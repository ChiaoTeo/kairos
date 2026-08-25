from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from rich.text import Text

from kairospy.surface.workbench.screens.activity import (
    ActivityKind,
    ActivityOutcome,
    ActivityRecord,
)
from kairospy.surface.workbench.screens.live import LiveBuffer
from kairospy.surface.workbench.widgets import (
    ActivityStream,
    ChoiceInteraction,
    InteractionRegion,
    interaction_copy_text,
)


class _ActivityApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ActivityStream(id="activities")


class _InteractionApp(App[None]):
    def __init__(self, interaction: ChoiceInteraction) -> None:
        super().__init__()
        self.interaction = interaction

    def compose(self) -> ComposeResult:
        yield InteractionRegion(self.interaction, id="interaction")


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


def test_activity_stream_redacts_visible_and_retained_content() -> None:
    secret = "123456789:abcdefghijklmnopqrstuvwxyz0123456789"

    async def run() -> tuple[str, str, str]:
        app = _ActivityApp()
        async with app.run_test() as pilot:
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="failure-1",
                    kind=ActivityKind.OPERATION,
                    outcome=ActivityOutcome.FAILURE,
                    title=f"token={secret}",
                    body=Text(f"provider rejected {secret}"),
                    copy_text=f"token={secret}",
                )
            )
            await pilot.pause()
            retained = stream.activities[0]
            return (
                stream.plain_text,
                stream.export_plain_text(),
                retained.copy_text or "",
            )

    visible, exported, retained = asyncio.run(run())

    assert secret not in visible
    assert secret not in exported
    assert secret not in retained
    assert "<redacted>" in visible


def test_activity_stream_redacts_direct_rich_log_writes() -> None:
    async def run() -> str:
        app = _ActivityApp()
        async with app.run_test() as pilot:
            stream = app.query_one(ActivityStream)
            stream.write("api_key=should-not-be-visible")
            await pilot.pause()
            return stream.plain_text

    visible = asyncio.run(run())

    assert "should-not-be-visible" not in visible
    assert "api_key=<redacted>" in visible


def test_interaction_region_redacts_visible_and_copyable_content() -> None:
    interaction = ChoiceInteraction(
        title="Provider error",
        summary=Text(
            "webhook https://open.feishu.cn/open-apis/bot/v2/hook/private-hook"
        ),
    )

    async def run() -> str:
        app = _InteractionApp(interaction)
        async with app.run_test() as pilot:
            app.query_one(InteractionRegion).present(interaction)
            await pilot.pause()
            content = app.query_one("#interaction-content")
            return str(content.content)

    visible = asyncio.run(run())
    copied = interaction_copy_text(interaction)

    assert "private-hook" not in visible
    assert "private-hook" not in copied
    assert "<redacted>" in visible
    assert "<redacted>" in copied


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
