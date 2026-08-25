"""Append-only, agent-readable records for one Workbench session."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import gettempdir
from threading import Lock
from typing import Any
from uuid import uuid4


_SENSITIVE_LINE = re.compile(
    r"(?i)(?:--)?"
    r"(api[_ -]?key|authorization|bearer|credential|password|secret|token)"
    r"(?:\s*[:=]\s*|\s+)([^\s,;]+)"
)
_AUTHORIZATION_LINE = re.compile(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+")


class WorkbenchTranscript:
    """Persist semantic UI events without making the terminal screen authoritative."""

    def __init__(self, path: Path | None) -> None:
        self.session_id = f"wb-{uuid4().hex[:12]}"
        self.path = path
        self._events: list[dict[str, Any]] = []
        self._started_operation_ids: set[str] = set()
        self._lock = Lock()

    @classmethod
    def create(
        cls,
        state: Any,
        explicit_path: Path | None = None,
    ) -> "WorkbenchTranscript":
        session_id = f"wb-{uuid4().hex[:12]}"
        path = explicit_path
        owner = getattr(state, "owner", None)
        if path is None and owner is not None:
            path = Path(owner.paths.root) / "logs" / "workbench" / f"{session_id}.jsonl"
        elif path is None:
            path = Path(gettempdir()) / "kairos-workbench" / f"{session_id}.jsonl"
        transcript = cls(path)
        transcript.session_id = session_id
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.touch(mode=0o600, exist_ok=True)
                os.chmod(path, 0o600)
            except OSError:
                transcript.path = None
        transcript._write_current_pointer()
        transcript.record(
            "session_started",
            workspace_id=getattr(state, "workspace_id", "未选择工作区"),
            workspace=(
                str(workspace_arg)
                if (workspace_arg := getattr(state, "workspace_arg", None))
                else None
            ),
        )
        return transcript

    def _write_current_pointer(self) -> None:
        if self.path is None:
            return
        pointer = self.path.parent / "current.json"
        temporary = pointer.with_suffix(".tmp")
        value = json.dumps(
            {"session_id": self.session_id, "transcript": str(self.path)},
            ensure_ascii=False,
        )
        try:
            temporary.write_text(value + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(pointer)
        except OSError:
            temporary.unlink(missing_ok=True)

    def record(self, event: str, **fields: Any) -> None:
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "event": event,
            **{key: _sanitize(value) for key, value in fields.items()},
        }
        encoded = json.dumps(item, ensure_ascii=False, default=str)
        with self._lock:
            self._events.append(item)
            if self.path is not None:
                try:
                    with self.path.open("a", encoding="utf-8") as output:
                        output.write(encoded + "\n")
                except OSError:
                    self.path = None

    def record_output(self, *, screen: str, widget: str | None, text: str) -> None:
        value = redact_text(text).strip()
        if value:
            self.record("output", screen=screen, widget=widget, text=value)

    def claim_operation(self, operation_id: str) -> bool:
        """Allow one immutable operation intent to be recorded exactly once."""

        with self._lock:
            if operation_id in self._started_operation_ids:
                return False
            self._started_operation_ids.add(operation_id)
            return True

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        """Return an immutable snapshot for tests and in-process diagnostics."""

        with self._lock:
            return tuple(dict(item) for item in self._events)


def redact_text(value: str) -> str:
    """Remove common credential assignments from user-shareable output."""

    value = _AUTHORIZATION_LINE.sub(r"\1<redacted>", value)
    return _SENSITIVE_LINE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


__all__ = ["WorkbenchTranscript", "redact_text"]
