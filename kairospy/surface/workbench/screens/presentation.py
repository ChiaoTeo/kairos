"""Shared, business-neutral presentation mechanics for Workbench results."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text


class ResultTone(StrEnum):
    """Visual tone for a conclusion; it does not encode business state."""

    NEUTRAL = "neutral"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    PENDING = "pending"
    PREVIEW = "preview"


_TONE_STYLES = {
    ResultTone.NEUTRAL: "bold",
    ResultTone.SUCCESS: "bold green",
    ResultTone.WARNING: "bold yellow",
    ResultTone.ERROR: "bold red",
    ResultTone.PENDING: "bold cyan",
    ResultTone.PREVIEW: "bold yellow",
}


def conclusion(value: str, *, tone: ResultTone = ResultTone.NEUTRAL) -> Text:
    """Render the one sentence that answers the user's question."""

    return Text(value, style=_TONE_STYLES[tone])


def facts(rows: Iterable[tuple[str, RenderableType]]) -> Table:
    """Render compact facts without introducing another result container."""

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows:
        table.add_row(label, value)
    return table


def section(title: str, body: RenderableType) -> Group:
    """Render one borderless Activity section."""

    return Group(Text(title, style="bold cyan"), body)


def next_steps(items: Iterable[str]) -> Group:
    """Render stable numbered recovery or continuation actions."""

    lines = tuple(Text(f"{index}. {item}") for index, item in enumerate(items, 1))
    return Group(Text("建议下一步", style="bold cyan"), *lines)


def local_time_from_unix_nanos(value: int) -> str:
    """Format an explicitly nanosecond Unix timestamp in the local timezone."""

    instant = datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)
    return instant.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def duration_from_nanos(value: int) -> str:
    """Format an explicitly nanosecond duration without guessing its unit."""

    return _duration_seconds(value / 1_000_000_000)


def duration_from_millis(value: int | float) -> str:
    """Format an explicitly millisecond duration without guessing its unit."""

    return _duration_seconds(float(value) / 1_000)


def duration_from_seconds(value: int | float) -> str:
    """Format an explicitly second duration."""

    return _duration_seconds(float(value))


def count(value: int) -> str:
    """Format a count with stable grouping."""

    return f"{value:,}"


def percentage(numerator: int, denominator: int) -> str:
    """Format a ratio while preserving the no-sample distinction."""

    if denominator == 0:
        return "暂无样本"
    return f"{numerator / denominator:.1%}（{numerator:,}/{denominator:,}）"


def _duration_seconds(seconds: float) -> str:
    magnitude = abs(seconds)
    if magnitude < 0.001:
        return f"{seconds * 1_000_000:.0f} µs"
    if magnitude < 1:
        return f"{seconds * 1_000:.3f} ms"
    if magnitude < 60:
        return f"{seconds:.3f}".rstrip("0").rstrip(".") + " 秒"
    if magnitude < 3_600:
        return f"{seconds / 60:.1f} 分钟"
    return f"{seconds / 3_600:.1f} 小时"


__all__ = [
    "ResultTone",
    "conclusion",
    "count",
    "duration_from_millis",
    "duration_from_nanos",
    "duration_from_seconds",
    "facts",
    "local_time_from_unix_nanos",
    "next_steps",
    "percentage",
    "section",
]
