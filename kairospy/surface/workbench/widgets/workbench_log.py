"""Typed Activity Stream backed by one RichLog."""

from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from textual.widgets import RichLog

from ..screens.activity import ActivityOutcome, ActivityRecord


class ActivityStream(RichLog):
    """Append terminal activities without owning business or interaction state."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._activities: list[ActivityRecord] = []
        self._new_activity_count = 0
        self._following = True

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

        self._activities.append(activity)
        super().write(_activity_renderable(activity), scroll_end=self._following)
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
            )

    def clear_visible_history(self) -> None:
        """Clear only the visible session history, never the transcript/artifacts."""

        self._activities.clear()
        self._new_activity_count = 0
        self.clear()

    def export_plain_text(self) -> str:
        sections = []
        for activity in self._activities:
            body = activity.copy_text or activity.audit_summary
            sections.append(
                activity.title if not body else f"{activity.title}\n\n{body}"
            )
        return "\n\n".join(sections)

    def resume_follow(self) -> None:
        self._following = True
        self._new_activity_count = 0
        self.scroll_end(animate=False, immediate=True, x_axis=False)

    def pause_follow(self) -> None:
        """Keep the current viewport stable while older output is inspected."""

        self._following = False


def _activity_renderable(activity: ActivityRecord) -> Panel:
    marker, border = {
        ActivityOutcome.SUCCESS: ("✓", "green"),
        ActivityOutcome.FAILURE: ("✗", "red"),
        ActivityOutcome.CANCELLED: ("■", "yellow"),
        ActivityOutcome.NOTICE: ("•", "cyan"),
    }[activity.outcome]
    body = activity.body or Text(activity.copy_text or activity.audit_summary or "")
    return Panel(body, title=f"{marker} {activity.title}", border_style=border)


def renderable_plain_text(content: Any, width: int = 100) -> str:
    """Render a Rich value to stable plain text for copy/transcript boundaries."""

    output = StringIO()
    console = Console(
        file=output,
        record=True,
        width=width,
        color_system=None,
        force_terminal=False,
    )
    console.print(content, markup=False, highlight=False)
    return output.getvalue()


__all__ = ["ActivityStream", "renderable_plain_text"]
