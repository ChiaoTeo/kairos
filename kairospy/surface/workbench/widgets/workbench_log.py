"""Typed Activity Stream backed by one RichLog."""

from __future__ import annotations

from dataclasses import dataclass, replace
import shlex
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual.events import Resize
from textual.timer import Timer
from textual.widgets import RichLog

from ..safety import redact_renderable, renderable_plain_text
from ..screens.activity import ActivityOutcome, ActivityRecord
from kairospy.surface.presentation import redact_cli_arguments, redact_text


_REFLOW_DEBOUNCE_SECONDS = 0.075


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

    @property
    def activities(self) -> tuple[ActivityRecord, ...]:
        return tuple(self._activities)

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
        self._append_rendered_activity(
            activity,
            separated=len(self._activities) > 1,
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
        self._append_rendered_live((), scroll_end=self._following)

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
        for index, activity in enumerate(self._activities):
            self._append_rendered_activity(
                activity,
                separated=index > 0,
                scroll_end=False,
            )
        if title is not None:
            self._append_rendered_live(
                self._live_lines,
                separated=bool(self._activities),
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
        separated: bool,
        scroll_end: bool,
    ) -> None:
        start = len(self.lines)
        super().write(
            _activity_renderable(activity, separated=separated),
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
            _live_header(title, separated=separated),
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

    def export_plain_text(self) -> str:
        sections = []
        for activity in self._activities:
            body = (
                renderable_plain_text(_content_renderable(activity.body))
                if activity.body is not None
                else activity.copy_text or activity.audit_summary
            )
            values = [activity.title]
            if body:
                values.append(body)
            if activity.equivalent_command:
                values.append("重新执行\n$ " + shlex.join(activity.equivalent_command))
            sections.append("\n\n".join(values))
        return "\n\n".join(sections)

    def resume_follow(self) -> None:
        self._following = True
        self._new_activity_count = 0
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
    activity: ActivityRecord, *, separated: bool
) -> RenderableType:
    marker, border = {
        ActivityOutcome.SUCCESS: ("✓", "green"),
        ActivityOutcome.FAILURE: ("✗", "red"),
        ActivityOutcome.CANCELLED: ("■", "yellow"),
        ActivityOutcome.NOTICE: ("•", "cyan"),
    }[activity.outcome]
    header = Text()
    header.append(marker, style=f"bold {border}")
    header.append(f" {activity.title}", style="bold")
    values: list[RenderableType] = []
    if separated:
        values.append(Rule(style="grey37"))
    values.append(header)
    body = activity.body or Text(activity.copy_text or activity.audit_summary or "")
    if body:
        values.extend((Text(""), _content_renderable(body)))
    if activity.equivalent_command:
        command = Text()
        command.append("重新执行\n", style="dim")
        command.append("$ ", style="dim")
        command.append(shlex.join(activity.equivalent_command), style="cyan")
        values.extend((Text(""), command))
    return Group(*values)


def _live_header(title: str, *, separated: bool = True) -> RenderableType:
    values: list[RenderableType] = []
    if separated:
        values.append(Rule(style="grey37"))
    header = Text()
    header.append("●", style="bold cyan")
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


__all__ = ["ActivityStream", "renderable_plain_text"]
