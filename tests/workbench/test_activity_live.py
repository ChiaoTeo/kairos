from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
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


class _ResizingActivityApp(App[None]):
    CSS = """
    Screen { layout: vertical; }
    ActivityStream { height: 1fr; min-height: 2; }
    #changing-footer { height: 1; }
    """

    def compose(self) -> ComposeResult:
        yield ActivityStream(id="activities")
        yield Static("", id="changing-footer")


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
                scope_label="trader / 行情 / AAPL",
                created_at=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
                artifact_path=Path("market-history/AAPL.jsonl"),
                equivalent_command=(
                    "kairos-market-cli",
                    "standalone",
                    "download",
                    "--symbol",
                    "AAPL",
                ),
            )
            stream.append_activity(activity)
            await pilot.pause()
            return stream.activities, stream.export_plain_text()

    activities, exported = asyncio.run(run())

    assert len(activities) == 1
    assert activities[0].activity_id == "operation-1"
    assert "下载历史行情 · AAPL" in exported
    assert "market-history/AAPL.jsonl" in exported
    assert "产物" in exported
    assert "范围：trader / 行情 / AAPL" in exported
    assert "时间：" in exported
    assert "重新执行" in exported
    assert "kairos-market-cli standalone download --symbol AAPL" in exported


def test_activity_stream_uses_dividers_and_redacts_equivalent_commands() -> None:
    async def run() -> tuple[str, tuple[str, ...] | None]:
        app = _ActivityApp()
        async with app.run_test(size=(80, 24)) as pilot:
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="operation-1",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title="AAPL · quote",
                    body=Text("买价 309.18"),
                )
            )
            stream.append_activity(
                ActivityRecord(
                    activity_id="operation-2",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.FAILURE,
                    title="AAPL · trade",
                    body=Text("请求失败"),
                    equivalent_command=(
                        "kairos-market-cli",
                        "--token",
                        "top-secret",
                        "standalone",
                        "once",
                    ),
                )
            )
            await pilot.pause()
            return stream.plain_text, stream.activities[-1].equivalent_command

    visible, command = asyncio.run(run())

    assert "────────────────" in visible
    assert "╭" not in visible
    assert "重新执行" in visible
    assert "top-secret" not in visible
    assert command == (
        "kairos-market-cli",
        "--token",
        "<redacted>",
        "standalone",
        "once",
    )


def test_activity_stream_reserves_a_stable_gutter_for_the_whole_activity() -> None:
    async def run() -> tuple[str, str]:
        app = _ActivityApp()
        async with app.run_test(size=(60, 20)) as pilot:
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="quote-1",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title="AAPL · quote",
                    body=Text("买盘 BID\n309.51"),
                )
            )
            await pilot.pause()
            unfocused = stream.plain_text
            stream.focus()
            assert stream.focus_sequence(1)
            await pilot.pause()
            return unfocused, stream.plain_text

    unfocused, focused = asyncio.run(run())

    assert "[A001]" in unfocused
    assert "  买盘 BID" in unfocused
    assert "┃ [A001]" in focused
    assert "  买盘 BID" in focused


def test_activity_stream_compacts_long_commands_but_exports_the_full_command() -> None:
    async def run() -> tuple[str, str]:
        app = _ActivityApp()
        async with app.run_test(size=(60, 20)) as pilot:
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="quote-1",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title="AAPL · quote",
                    equivalent_command=(
                        "kairos",
                        "market",
                        "--workspace",
                        "/workspace/.kairos",
                        "standalone",
                        "once",
                        "--market-id",
                        "market:nasdaq:equity:AAPL:USD",
                        "--observation-kind",
                        "quote",
                    ),
                )
            )
            await pilot.pause()
            return stream.plain_text, stream.export_plain_text()

    visible, exported = asyncio.run(run())

    assert "$ kairos market … --observation-kind quote" in visible
    assert "C 复制完整 Activity" in visible
    assert "market:nasdaq:equity:AAPL:USD" not in visible
    assert "market:nasdaq:equity:AAPL:USD" in exported


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


def test_activity_stream_follows_new_bottom_after_layout_shrinks() -> None:
    async def run() -> tuple[float, float]:
        app = _ResizingActivityApp()
        async with app.run_test(size=(50, 12)) as pilot:
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

            assert stream.scroll_y == stream.max_scroll_y
            app.query_one("#changing-footer", Static).styles.height = 6
            await pilot.pause()
            return stream.scroll_y, stream.max_scroll_y

    scroll_y, max_scroll_y = asyncio.run(run())

    assert scroll_y == max_scroll_y


def test_activity_stream_preserves_browsed_position_when_layout_shrinks() -> None:
    async def run() -> tuple[float, float]:
        app = _ResizingActivityApp()
        async with app.run_test(size=(50, 12)) as pilot:
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
            stream.pause_follow()
            stream.scroll_page_up(animate=False)
            await pilot.pause()
            browsed_y = stream.scroll_y

            app.query_one("#changing-footer", Static).styles.height = 6
            await pilot.pause()
            return browsed_y, stream.scroll_y

    browsed_y, resized_y = asyncio.run(run())

    assert resized_y == browsed_y


def test_activity_stream_reflows_retained_activities_on_terminal_width_change() -> None:
    async def run() -> tuple[int, int, int, int, int, tuple[str, ...]]:
        app = _ActivityApp()
        async with app.run_test(size=(100, 24)) as pilot:
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="resize-source",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title="动态宽度",
                    body=Text("可重复渲染的内容 " * 20),
                )
            )
            await pilot.pause()
            wide_lines = len(stream.lines)

            await pilot.resize_terminal(50, 16)
            await pilot.pause(0.1)
            narrow_lines = len(stream.lines)
            narrow_render_width = max(line.cell_length for line in stream.lines)
            narrow_content_width = stream.scrollable_content_region.width

            await pilot.resize_terminal(100, 24)
            await pilot.pause(0.1)
            restored_lines = len(stream.lines)
            return (
                wide_lines,
                narrow_lines,
                restored_lines,
                narrow_render_width,
                narrow_content_width,
                tuple(activity.activity_id for activity in stream.activities),
            )

    (
        wide_lines,
        narrow_lines,
        restored_lines,
        narrow_render_width,
        narrow_content_width,
        activity_ids,
    ) = asyncio.run(run())

    assert narrow_lines > wide_lines
    assert restored_lines == wide_lines
    assert narrow_render_width <= narrow_content_width
    assert activity_ids == ("resize-source",)


def test_activity_stream_resize_preserves_semantic_browsing_anchor() -> None:
    async def run() -> tuple[str | None, str | None]:
        app = _ActivityApp()
        async with app.run_test(size=(100, 18)) as pilot:
            stream = app.query_one(ActivityStream)
            for index in range(20):
                stream.append_activity(
                    ActivityRecord(
                        activity_id=f"activity-{index}",
                        kind=ActivityKind.OPERATION,
                        outcome=ActivityOutcome.SUCCESS,
                        title=f"activity {index}",
                        body=Text(f"details for activity {index} " * 5),
                    )
                )
            await pilot.pause()
            stream.pause_follow()
            stream.scroll_to(y=stream.max_scroll_y // 2, animate=False, immediate=True)
            await pilot.pause()
            before = stream.viewport_activity_id

            await pilot.resize_terminal(60, 18)
            await pilot.pause(0.1)
            return before, stream.viewport_activity_id

    before, after = asyncio.run(run())

    assert before is not None
    assert after == before


def test_activity_stream_does_not_own_live_output() -> None:
    stream = ActivityStream()

    assert not hasattr(stream, "begin_live_stream")
    assert not hasattr(stream, "append_live_lines")


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


def test_live_buffer_redacts_before_retaining_lines() -> None:
    buffer = LiveBuffer("service/market")

    buffer.append("request api_key=private-value")

    assert "private-value" not in buffer.copy_text()


def test_live_output_is_not_part_of_activity_export() -> None:
    async def run() -> tuple[str, tuple[ActivityRecord, ...]]:
        app = _ActivityApp()
        async with app.run_test(size=(60, 12)):
            stream = app.query_one(ActivityStream)
            stream.append_activity(
                ActivityRecord(
                    activity_id="terminal-only",
                    kind=ActivityKind.QUERY,
                    outcome=ActivityOutcome.SUCCESS,
                    title="Market 状态",
                    body=Text("已停止"),
                )
            )
            return stream.export_plain_text(), stream.activities

    exported, activities = asyncio.run(run())
    assert "Market 状态" in exported
    assert len(activities) == 1
