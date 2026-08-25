"""Typed terminal activities retained in the Workbench's visible history."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rich.console import RenderableType


class ActivityKind(StrEnum):
    """Closed set of visible terminal activity categories."""

    OPERATION = "operation"
    QUERY = "query"
    ARTIFACT = "artifact"
    SYSTEM = "system"


class ActivityOutcome(StrEnum):
    """Terminal outcome shown in the Activity Stream."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    NOTICE = "notice"


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """One stable, copyable result retained in the current Workbench session."""

    activity_id: str
    kind: ActivityKind
    outcome: ActivityOutcome
    title: str
    body: RenderableType | None = None
    copy_text: str | None = None
    audit_summary: str | None = None
    artifact_path: Path | None = None
    equivalent_command: tuple[str, ...] | None = None


__all__ = ["ActivityKind", "ActivityOutcome", "ActivityRecord"]
