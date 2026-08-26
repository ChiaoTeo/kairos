"""Typed Activity Stream backed by one RichLog."""

from __future__ import annotations

from dataclasses import dataclass, replace
import shlex
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual.binding import Binding
from textual.events import Click, Resize
from textual.message import Message
from textual.timer import Timer
from textual.widgets import RichLog

from ..safety import redact_renderable, renderable_plain_text
from ..screens.activity import ActivityOutcome, ActivityRecord
from ..theme import NORD_COLORS, RichThemeColors, rich_theme_colors
from kairospy.surface.presentation import redact_cli_arguments, redact_text


_REFLOW_DEBOUNCE_SECONDS = 0.075


class ActivitySelectionChanged(Message):
    """Report a change to the transient Workbench Activity selection."""

    def __init__(self, count: int, *, focus_lost: bool = False) -> None:
        super().__init__()
        self.count = count
        self.focus_lost = focus_lost


class ActivityCopyRequested(Message):
    """Ask the owning screen to copy already-redacted Activity text."""

    def __init__(self, text: str, label: str) -> None:
        super().__init__()
        self.text = text
        self.label = label


class ActivityFocusExitRequested(Message):
    """Ask the owning screen to return keyboard focus to its command input."""


@dataclass(frozen=True, slots=True)
class _RenderedRange:
    activity_id: str | None
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _ViewportAnchor:
    activity_id: str | None
    line_offset: int
    fallback_y: float


class ActivityStream(RichLog):
    """Append terminal activities without owning business or interaction state."""

    BINDINGS = [
        Binding("up", "cursor_previous", "上一条活动", show=False),
        Binding("down", "cursor_next", "下一条活动", show=False),
        Binding("shift+up", "extend_previous", "向上扩展选择", show=False),
        Binding("shift+down", "extend_next", "向下扩展选择", show=False),
        Binding("space", "toggle_selected", "选择活动", show=False),
        Binding("c", "copy_selected", "复制所选活动", show=False),
        Binding("super+c", "copy_selected", "复制所选活动", show=False),
        Binding("ctrl+a", "select_all_activities", "选择全部活动", show=False),
        Binding("home", "cursor_first", "第一条活动", show=False),
        Binding("end", "cursor_last", "最后一条活动", show=False),
        Binding("pageup", "cursor_page_up", "向上翻页", show=False),
        Binding("pagedown", "cursor_page_down", "向下翻页", show=False),
        Binding("escape", "exit_selection", "返回命令输入", show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # RichLog defaults to a 78-cell render width. That is useful for logs with
        # horizontal scrolling, but this Workbench intentionally wraps and hides
        # horizontal overflow. Use the actual content width at every render.
        kwargs.setdefault("min_width", 1)
        super().__init__(*args, **kwargs)
        self._activities: list[ActivityRecord] = []
        self._new_activity_count = 0
        self._following = True
        self._live_title: str | None = None
        self._live_lines: tuple[str, ...] = ()
        self._live_line_count = 0
        self._rendered_ranges: list[_RenderedRange] = []
        self._rendered_width: int | None = None
        self._reflow_timer: Timer | None = None
        self._display_sequence_by_id: dict[str, int] = {}
        self._next_display_sequence = 1
        self._cursor_activity_id: str | None = None
        self._selected_activity_ids: set[str] = set()
        self._selection_anchor_id: str | None = None
        self._default_copy_target_suppressed = False

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)

    def _theme_changed(self, _theme: object) -> None:
        self._rebuild_visible()

    def _theme_colors(self) -> RichThemeColors:
        return rich_theme_colors(self.app.current_theme)

    @property
    def activities(self) -> tuple[ActivityRecord, ...]:
        return tuple(self._activities)

    @property
    def selected_count(self) -> int:
        return len(self._selected_activity_ids)

    @property
    def cursor_sequence(self) -> int | None:
        if self._cursor_activity_id is None:
            return None
        return self._display_sequence_by_id.get(self._cursor_activity_id)

    @property
    def new_activity_count(self) -> int:
        return self._new_activity_count

    @property
    def viewport_activity_id(self) -> str | None:
        """Return the completed activity currently anchored at the viewport top."""

        top = int(self.scroll_y)
        for rendered_range in self._rendered_ranges:
            if rendered_range.start <= top < rendered_range.end:
                return rendered_range.activity_id
        return None

    @property
    def plain_text(self) -> str:
        """Return the complete log as copyable text, including scrolled content."""

        return "\n".join(line.text.rstrip() for line in self.lines).strip()

    def append_activity(self, activity: ActivityRecord) -> None:
        """Append one terminal activity and preserve an explicitly scrolled view."""

        activity = _redact_activity(activity)
        self._activities.append(activity)
        display_sequence = self._next_display_sequence
        self._next_display_sequence += 1
        self._display_sequence_by_id[activity.activity_id] = display_sequence
        self._append_rendered_activity(
            activity,
            scroll_end=self._following,
        )
        if self._following:
            self._new_activity_count = 0
        else:
            self._new_activity_count += 1
        transcript = getattr(self.app, "transcript", None)
        if transcript is not None:
            transcript.record(
                "activity",
                activity_id=activity.activity_id,
                display_sequence=display_sequence,
                kind=activity.kind.value,
                outcome=activity.outcome.value,
                title=activity.title,
                summary=activity.audit_summary or activity.copy_text,
                artifact_path=(
                    str(activity.artifact_path) if activity.artifact_path else None
                ),
                equivalent_command=(
                    list(activity.equivalent_command)
                    if activity.equivalent_command
                    else None
                ),
            )

    def clear_visible_history(self) -> None:
        """Clear only the visible session history, never the transcript/artifacts."""

        self._activities.clear()
        self._display_sequence_by_id.clear()
        self._cursor_activity_id = None
        self._selected_activity_ids.clear()
        self._selection_anchor_id = None
        self._default_copy_target_suppressed = False
        self._rendered_ranges.clear()
        self._new_activity_count = 0
        self.clear()
        if self._live_title is not None:
            self._live_lines = ()
            self._live_line_count = 0
            self._append_rendered_live((), separated=False)

    @property
    def live_title(self) -> str | None:
        return self._live_title

    def begin_live_stream(self, title: str) -> None:
        """Open one transient stream without adding it to Activity history."""

        self._live_title = redact_text(title)
        self._live_lines = ()
        self._live_line_count = 0
        self._append_rendered_live(
            (),
            separated=False,
            scroll_end=self._following,
        )

    def append_live_lines(
        self, lines: tuple[str, ...], *, retained_lines: tuple[str, ...]
    ) -> None:
        """Append deltas and rebuild only when the bounded tail drops old lines."""

        if self._live_title is None or not lines:
            return
        safe_lines = tuple(redact_text(line) for line in lines)
        safe_retained = tuple(redact_text(line) for line in retained_lines)
        self._live_lines = safe_retained
        if self._live_line_count + len(safe_lines) > len(safe_retained):
            self._rebuild_visible()
            return
        for line in safe_lines:
            super().write(
                Text(line),
                scroll_end=self._following,
            )
        self._live_line_count += len(safe_lines)
        self._refresh_live_range()

    def clear_live_stream(self) -> None:
        """Clear the transient window while preserving completed activities."""

        if self._live_title is not None:
            self._live_lines = ()
            self._rebuild_visible()

    def end_live_stream(self) -> None:
        """Remove transient lines; callers may then append one terminal summary."""

        if self._live_title is None:
            return
        self._live_title = None
        self._live_lines = ()
        self._live_line_count = 0
        self._rebuild_visible()

    def _rebuild_visible(self) -> None:
        title = self._live_title
        anchor = self._capture_viewport_anchor()
        self.clear()
        self._rendered_ranges.clear()
        for activity in self._activities:
            self._append_rendered_activity(
                activity,
                scroll_end=False,
            )
        if title is not None:
            self._append_rendered_live(
                self._live_lines,
                separated=False,
                scroll_end=False,
            )
        self._live_line_count = len(self._live_lines)
        self._rendered_width = self.scrollable_content_region.width
        if self._following:
            self.call_after_refresh(self._scroll_to_latest)
        else:
            self.call_after_refresh(self._restore_viewport_anchor, anchor)

    def _append_rendered_activity(
        self,
        activity: ActivityRecord,
        *,
        scroll_end: bool,
    ) -> None:
        start = len(self.lines)
        selected = activity.activity_id in self._selected_activity_ids
        default_copy_target = (
            self.has_focus
            and not self._selected_activity_ids
            and not self._default_copy_target_suppressed
            and activity.activity_id == self._cursor_activity_id
        )
        super().write(
            _activity_renderable(
                activity,
                colors=self._theme_colors(),
                display_sequence=self._display_sequence_by_id[activity.activity_id],
                current=(
                    self.has_focus and activity.activity_id == self._cursor_activity_id
                ),
                selected=selected,
                default_copy_target=default_copy_target,
                selection_background=self.app.current_theme.panel or "#434c5e",
            ),
            expand=True,
            scroll_end=scroll_end,
        )
        self._rendered_ranges.append(
            _RenderedRange(activity.activity_id, start, len(self.lines))
        )

    def _append_rendered_live(
        self,
        lines: tuple[str, ...],
        *,
        separated: bool = True,
        scroll_end: bool = False,
    ) -> None:
        title = self._live_title
        if title is None:
            return
        start = len(self.lines)
        super().write(
            _live_header(
                title,
                separated=separated,
                colors=self._theme_colors(),
            ),
            scroll_end=False,
        )
        for line in lines:
            super().write(Text(line), scroll_end=False)
        self._rendered_ranges.append(_RenderedRange(None, start, len(self.lines)))
        if scroll_end:
            self.scroll_end(animate=False, immediate=False, x_axis=False)

    def _refresh_live_range(self) -> None:
        if self._rendered_ranges and self._rendered_ranges[-1].activity_id is None:
            current = self._rendered_ranges[-1]
            self._rendered_ranges[-1] = replace(current, end=len(self.lines))

    def _capture_viewport_anchor(self) -> _ViewportAnchor:
        top = int(self.scroll_y)
        for rendered_range in self._rendered_ranges:
            if rendered_range.start <= top < rendered_range.end:
                return _ViewportAnchor(
                    rendered_range.activity_id,
                    top - rendered_range.start,
                    self.scroll_y,
                )
        return _ViewportAnchor(None, 0, self.scroll_y)

    def _restore_viewport_anchor(self, anchor: _ViewportAnchor) -> None:
        for rendered_range in self._rendered_ranges:
            if rendered_range.activity_id == anchor.activity_id:
                available_offset = max(rendered_range.end - rendered_range.start - 1, 0)
                target = rendered_range.start + min(
                    anchor.line_offset, available_offset
                )
                self.scroll_to(y=target, animate=False, immediate=True)
                return
        self.scroll_to(
            y=min(anchor.fallback_y, self.max_scroll_y),
            animate=False,
            immediate=True,
        )

    def export_plain_text(
        self, activities: tuple[ActivityRecord, ...] | None = None
    ) -> str:
        sections = []
        for activity in activities if activities is not None else self._activities:
            body = (
                renderable_plain_text(_content_renderable(activity.body))
                if activity.body is not None
                else activity.copy_text or activity.audit_summary
            )
            sequence = self._display_sequence_by_id[activity.activity_id]
            values = [f"[A{sequence:03d}] {activity.title}"]
            if body:
                values.append(body)
            if activity.equivalent_command:
                values.append("重新执行\n$ " + shlex.join(activity.equivalent_command))
            sections.append("\n\n".join(values))
        return "\n\n".join(sections)

    def activity_for_sequence(self, sequence: int) -> ActivityRecord | None:
        """Return one retained Activity by its stable session display sequence."""

        return next(
            (
                activity
                for activity in self._activities
                if self._display_sequence_by_id.get(activity.activity_id) == sequence
            ),
            None,
        )

    def activities_in_sequence_range(
        self, start: int, end: int
    ) -> tuple[ActivityRecord, ...]:
        """Return retained Activities in one inclusive display sequence range."""

        return tuple(
            activity
            for activity in self._activities
            if start <= self._display_sequence_by_id.get(activity.activity_id, 0) <= end
        )

    def selected_activities(self) -> tuple[ActivityRecord, ...]:
        return tuple(
            activity
            for activity in self._activities
            if activity.activity_id in self._selected_activity_ids
        )

    def copy_target_activities(self) -> tuple[ActivityRecord, ...]:
        """Return explicit selections, or the current implicit copy target."""

        selected = self.selected_activities()
        if (
            selected
            or self._cursor_activity_id is None
            or self._default_copy_target_suppressed
        ):
            return selected
        current = next(
            (
                activity
                for activity in self._activities
                if activity.activity_id == self._cursor_activity_id
            ),
            None,
        )
        return (current,) if current is not None else ()

    def focus_sequence(self, sequence: int) -> bool:
        activity = self.activity_for_sequence(sequence)
        if activity is None:
            return False
        self._cursor_activity_id = activity.activity_id
        self._default_copy_target_suppressed = False
        self.pause_follow()
        self._rebuild_visible()
        self.call_after_refresh(self._scroll_cursor_into_view)
        return True

    def scroll_lines(self, delta: int) -> None:
        """Scroll an exact number of rendered lines within the retained viewport."""

        if delta < 0:
            self.pause_follow()
        target = min(max(self.scroll_y + delta, 0), self.max_scroll_y)
        self.scroll_to(y=target, animate=False, immediate=True)

    def on_focus(self) -> None:
        if self._activities and self._cursor_activity_id is None:
            self._cursor_activity_id = self._activities[-1].activity_id
        self._default_copy_target_suppressed = False
        if self._activities:
            self.call_after_refresh(self._rebuild_visible)

    def on_blur(self) -> None:
        had_selection = bool(self._selected_activity_ids)
        self._selected_activity_ids.clear()
        self._selection_anchor_id = None
        self._default_copy_target_suppressed = False
        if self._activities:
            self.call_after_refresh(self._rebuild_visible)
        if had_selection:
            self.post_message(ActivitySelectionChanged(0, focus_lost=True))

    def on_click(self, event: Click) -> None:
        """Focus and select semantic Activities from terminal mouse clicks."""

        if event.button != 1:
            return
        offset = event.get_content_offset(self)
        if offset is None:
            return
        rendered_y = int(self.scroll_y) + offset.y
        activity_id = next(
            (
                item.activity_id
                for item in self._rendered_ranges
                if item.activity_id is not None and item.start <= rendered_y < item.end
            ),
            None,
        )
        if activity_id is None:
            return
        previous_cursor = self._cursor_activity_id
        self.app.set_focus(self)
        self._cursor_activity_id = activity_id
        self.pause_follow()
        if event.shift:
            anchor_id = self._selection_anchor_id or previous_cursor or activity_id
            self._select_range(anchor_id, activity_id)
            self._default_copy_target_suppressed = False
        elif event.meta or event.ctrl:
            if activity_id in self._selected_activity_ids:
                self._selected_activity_ids.remove(activity_id)
                self._default_copy_target_suppressed = not self._selected_activity_ids
            else:
                self._selected_activity_ids.add(activity_id)
                self._default_copy_target_suppressed = False
            self._selection_anchor_id = activity_id
        else:
            if self._selected_activity_ids == {activity_id}:
                self._selected_activity_ids.clear()
                self._selection_anchor_id = None
                self._default_copy_target_suppressed = True
            else:
                self._selected_activity_ids = {activity_id}
                self._selection_anchor_id = activity_id
                self._default_copy_target_suppressed = False
        self._rebuild_visible()
        self.post_message(ActivitySelectionChanged(self.selected_count))
        event.stop()

    def action_cursor_previous(self) -> None:
        self._move_cursor(-1)

    def action_cursor_next(self) -> None:
        self._move_cursor(1)

    def action_cursor_first(self) -> None:
        self._move_cursor_to_index(0)

    def action_cursor_last(self) -> None:
        self._move_cursor_to_index(len(self._activities) - 1)

    def action_cursor_page_up(self) -> None:
        self.pause_follow()
        self.scroll_page_up(animate=False)
        self.call_after_refresh(self._move_cursor_to_viewport_top)

    def action_cursor_page_down(self) -> None:
        self.scroll_page_down(animate=False)
        self.call_after_refresh(self._move_cursor_to_viewport_top)

    def action_toggle_selected(self) -> None:
        activity_id = self._cursor_activity_id
        if activity_id is None:
            return
        if activity_id in self._selected_activity_ids:
            self._selected_activity_ids.remove(activity_id)
            self._default_copy_target_suppressed = not self._selected_activity_ids
        else:
            self._selected_activity_ids.add(activity_id)
            self._default_copy_target_suppressed = False
        self._selection_anchor_id = activity_id
        self._rebuild_visible()
        self.post_message(ActivitySelectionChanged(self.selected_count))

    def action_extend_previous(self) -> None:
        self._extend_selection(-1)

    def action_extend_next(self) -> None:
        self._extend_selection(1)

    def action_select_all_activities(self) -> None:
        self._selected_activity_ids = {
            activity.activity_id for activity in self._activities
        }
        self._default_copy_target_suppressed = False
        if self._cursor_activity_id is not None:
            self._selection_anchor_id = self._cursor_activity_id
        self._rebuild_visible()
        self.post_message(ActivitySelectionChanged(self.selected_count))

    def action_copy_selected(self) -> None:
        activities = self.copy_target_activities()
        if not activities:
            self.post_message(ActivityCopyRequested("", "当前活动"))
            return
        sequences = tuple(
            self._display_sequence_by_id[activity.activity_id]
            for activity in activities
        )
        label = _sequence_label(sequences)
        self.post_message(
            ActivityCopyRequested(self.export_plain_text(activities), label)
        )

    def action_exit_selection(self) -> None:
        self._selected_activity_ids.clear()
        self._selection_anchor_id = None
        self._default_copy_target_suppressed = False
        self._rebuild_visible()
        self.post_message(ActivitySelectionChanged(0))
        self.post_message(ActivityFocusExitRequested())

    def _move_cursor(self, delta: int) -> None:
        if not self._activities:
            return
        current_index = next(
            (
                index
                for index, activity in enumerate(self._activities)
                if activity.activity_id == self._cursor_activity_id
            ),
            len(self._activities) - 1,
        )
        self._move_cursor_to_index(current_index + delta)

    def _move_cursor_to_index(self, index: int) -> None:
        if not self._activities:
            return
        index = min(max(index, 0), len(self._activities) - 1)
        self._cursor_activity_id = self._activities[index].activity_id
        self._default_copy_target_suppressed = False
        self.pause_follow()
        self._rebuild_visible()
        self.call_after_refresh(self._scroll_cursor_into_view)

    def _move_cursor_to_viewport_top(self) -> None:
        activity_id = self.viewport_activity_id
        if activity_id is None:
            return
        self._cursor_activity_id = activity_id
        self._default_copy_target_suppressed = False
        self._rebuild_visible()

    def _extend_selection(self, delta: int) -> None:
        if not self._activities:
            return
        if self._cursor_activity_id is None:
            self._cursor_activity_id = self._activities[-1].activity_id
        if self._selection_anchor_id is None:
            self._selection_anchor_id = self._cursor_activity_id
        self._move_cursor(delta)
        self._select_range(self._selection_anchor_id, self._cursor_activity_id)
        self._rebuild_visible()
        self.post_message(ActivitySelectionChanged(self.selected_count))

    def _select_range(self, anchor_id: str, cursor_id: str) -> None:
        ids = [activity.activity_id for activity in self._activities]
        anchor = ids.index(anchor_id)
        cursor = ids.index(cursor_id)
        start, end = sorted((anchor, cursor))
        self._selected_activity_ids = set(ids[start : end + 1])
        self._default_copy_target_suppressed = False

    def _scroll_cursor_into_view(self) -> None:
        if self._cursor_activity_id is None:
            return
        rendered_range = next(
            (
                item
                for item in self._rendered_ranges
                if item.activity_id == self._cursor_activity_id
            ),
            None,
        )
        if rendered_range is None:
            return
        top = self.scroll_y
        height = max(self.scrollable_content_region.height, 1)
        if rendered_range.start < top:
            target = rendered_range.start
        elif rendered_range.end > top + height:
            target = max(rendered_range.end - height, rendered_range.start)
        else:
            return
        self.scroll_to(y=target, animate=False, immediate=True)

    def resume_follow(self) -> None:
        self._following = True
        self._new_activity_count = 0
        if self._activities:
            self._cursor_activity_id = self._activities[-1].activity_id
            self._default_copy_target_suppressed = False
            if self.has_focus:
                self._rebuild_visible()
        self.scroll_end(animate=False, immediate=True, x_axis=False)

    def pause_follow(self) -> None:
        """Keep the current viewport stable while older output is inspected."""

        self._following = False

    def on_resize(self, _event: Resize) -> None:
        """Reflow retained sources after width changes and preserve the viewport."""

        if self._following:
            self.scroll_end(animate=False, immediate=True, x_axis=False)
            self.call_after_refresh(self._scroll_to_latest)
        self.call_after_refresh(self._observe_content_width)

    def _observe_content_width(self) -> None:
        width = self.scrollable_content_region.width
        if width <= 0:
            return
        if self._rendered_width is None:
            self._rendered_width = width
            return
        if width == self._rendered_width:
            return
        if self._reflow_timer is not None:
            self._reflow_timer.stop()
        self._reflow_timer = self.set_timer(
            _REFLOW_DEBOUNCE_SECONDS,
            self._reflow_after_resize,
            name="activity-stream-reflow",
        )

    def _reflow_after_resize(self) -> None:
        self._reflow_timer = None
        width = self.scrollable_content_region.width
        if width <= 0 or width == self._rendered_width:
            return
        self._rebuild_visible()

    def _scroll_to_latest(self) -> None:
        if self._following:
            self.scroll_end(animate=False, immediate=True, x_axis=False)

    def write(
        self,
        content: Any,
        width: int | None = None,
        expand: bool = False,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
    ) -> "ActivityStream":
        """Redact content even when infrastructure writes outside ActivityRecord."""

        return super().write(
            redact_renderable(content),
            width=width,
            expand=expand,
            shrink=shrink,
            scroll_end=scroll_end,
            animate=animate,
        )


def _activity_renderable(
    activity: ActivityRecord,
    *,
    colors: RichThemeColors = NORD_COLORS,
    display_sequence: int = 0,
    current: bool = False,
    selected: bool = False,
    default_copy_target: bool = False,
    selection_background: str = "#434c5e",
) -> RenderableType:
    marker, border = {
        ActivityOutcome.SUCCESS: ("✓", colors.success),
        ActivityOutcome.FAILURE: ("×", colors.error),
        ActivityOutcome.CANCELLED: ("■", colors.warning),
        ActivityOutcome.NOTICE: ("•", colors.primary),
    }[activity.outcome]
    header = Text()
    header.append(f"[A{display_sequence:03d}] ", style="dim")
    header.append(marker, style=f"bold {border}")
    header.append(f" {activity.title}", style="bold")
    activity_values: list[RenderableType] = [header]
    body = activity.body or Text(activity.copy_text or activity.audit_summary or "")
    if body:
        activity_values.extend((Text(""), _content_renderable(body)))
    if activity.equivalent_command:
        command = Text()
        command_text, compacted = _display_equivalent_command(
            activity.equivalent_command
        )
        command.append("重新执行", style="dim")
        if compacted:
            command.append(" · C 复制完整 Activity", style="dim")
        command.append("\n", style="dim")
        command.append("$ ", style="dim")
        command.append(command_text, style=colors.primary)
        activity_values.extend((Text(""), command))
    gutter = Text()
    gutter.append("┃" if current or default_copy_target else " ", style=colors.primary)
    gutter.append("●" if selected else " ", style=f"bold {colors.primary}")
    selected_item = Table.grid(expand=True, padding=0)
    selected_item.add_column(width=2, no_wrap=True)
    selected_item.add_column(ratio=1)
    selected_item.add_row(
        gutter,
        Group(*activity_values),
        style=f"on {selection_background}" if selected or default_copy_target else None,
    )
    activity_renderable: RenderableType = selected_item
    return Group(activity_renderable, Rule(style="grey37"), Text(""))


def _live_header(
    title: str,
    *,
    separated: bool = True,
    colors: RichThemeColors = NORD_COLORS,
) -> RenderableType:
    values: list[RenderableType] = []
    if separated:
        values.append(Rule(style="grey37"))
    header = Text()
    header.append("●", style=f"bold {colors.primary}")
    header.append(f" {title}", style="bold")
    values.append(header)
    return Group(*values)


def _content_renderable(body: RenderableType) -> RenderableType:
    """Remove a redundant result Panel inside the divided activity stream."""

    if not isinstance(body, Panel):
        return body
    if body.title:
        return Group(Text(str(body.title), style="dim"), Text(""), body.renderable)
    return body.renderable


def _redact_activity(activity: ActivityRecord) -> ActivityRecord:
    body = redact_renderable(activity.body) if activity.body is not None else None
    return replace(
        activity,
        title=redact_text(activity.title),
        body=body,
        copy_text=(redact_text(activity.copy_text) if activity.copy_text else None),
        audit_summary=(
            redact_text(activity.audit_summary) if activity.audit_summary else None
        ),
        equivalent_command=(
            redact_cli_arguments(activity.equivalent_command)
            if activity.equivalent_command
            else None
        ),
    )


def _sequence_label(sequences: tuple[int, ...]) -> str:
    if not sequences:
        return "当前活动"
    if len(sequences) == 1:
        return f"A{sequences[0]:03d}"
    if sequences == tuple(range(sequences[0], sequences[-1] + 1)):
        return f"A{sequences[0]:03d}–A{sequences[-1]:03d}"
    return "、".join(f"A{sequence:03d}" for sequence in sequences)


def _display_equivalent_command(command: tuple[str, ...]) -> tuple[str, bool]:
    full_command = shlex.join(command)
    if len(full_command) <= 88 or len(command) <= 4:
        return full_command, False
    compact_command = " ".join(
        (shlex.join(command[:2]), "…", shlex.join(command[-2:]))
    )
    return compact_command, True


__all__ = [
    "ActivityCopyRequested",
    "ActivityFocusExitRequested",
    "ActivitySelectionChanged",
    "ActivityStream",
    "renderable_plain_text",
]
