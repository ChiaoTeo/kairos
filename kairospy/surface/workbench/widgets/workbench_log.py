"""Typed Activity Stream backed by one RichLog."""

from __future__ import annotations

from dataclasses import replace
import shlex
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual.widgets import RichLog

from ..safety import redact_renderable, renderable_plain_text
from ..screens.activity import ActivityOutcome, ActivityRecord
from kairospy.surface.presentation import redact_cli_arguments, redact_text


class ActivityStream(RichLog):
    """Append terminal activities without owning business or interaction state."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._activities: list[ActivityRecord] = []
        self._new_activity_count = 0
        self._following = True
        self._live_title: str | None = None
        self._live_line_count = 0

    @property
    def activities(self) -> tuple[ActivityRecord, ...]:
        return tuple(self._activities)

    @property
    def new_activity_count(self) -> int:
        return self._new_activity_count

    @property
    def plain_text(self) -> str:
        """Return the complete log as copyable text, including scrolled content."""

        return "\n".join(line.text.rstrip() for line in self.lines).strip()

    def append_activity(self, activity: ActivityRecord) -> None:
        """Append one terminal activity and preserve an explicitly scrolled view."""

        activity = _redact_activity(activity)
        self._activities.append(activity)
        super().write(
            _activity_renderable(activity, separated=len(self._activities) > 1),
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
        self._new_activity_count = 0
        self.clear()
        if self._live_title is not None:
            self._live_line_count = 0
            super().write(_live_header(self._live_title, separated=False))

    @property
    def live_title(self) -> str | None:
        return self._live_title

    def begin_live_stream(self, title: str) -> None:
        """Open one transient stream without adding it to Activity history."""

        self._live_title = redact_text(title)
        self._live_line_count = 0
        super().write(_live_header(self._live_title), scroll_end=self._following)

    def append_live_lines(
        self, lines: tuple[str, ...], *, retained_lines: tuple[str, ...]
    ) -> None:
        """Append deltas and rebuild only when the bounded tail drops old lines."""

        if self._live_title is None or not lines:
            return
        if self._live_line_count + len(lines) > len(retained_lines):
            self._rebuild_visible(retained_lines)
            return
        for line in lines:
            super().write(
                Text(redact_text(line)),
                scroll_end=self._following,
            )
        self._live_line_count += len(lines)

    def clear_live_stream(self) -> None:
        """Clear the transient window while preserving completed activities."""

        if self._live_title is not None:
            self._rebuild_visible(())

    def end_live_stream(self) -> None:
        """Remove transient lines; callers may then append one terminal summary."""

        if self._live_title is None:
            return
        self._live_title = None
        self._live_line_count = 0
        self._rebuild_visible(())

    def _rebuild_visible(self, live_lines: tuple[str, ...]) -> None:
        title = self._live_title
        previous_y = self.scroll_y
        self.clear()
        for index, activity in enumerate(self._activities):
            super().write(
                _activity_renderable(activity, separated=index > 0),
                scroll_end=False,
            )
        if title is not None:
            super().write(
                _live_header(title, separated=bool(self._activities)),
                scroll_end=False,
            )
            for line in live_lines:
                super().write(Text(redact_text(line)), scroll_end=False)
        self._live_line_count = len(live_lines)
        if self._following:
            self.scroll_end(animate=False, immediate=True, x_axis=False)
        else:
            self.scroll_to(y=previous_y, animate=False, immediate=True)

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
