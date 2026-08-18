from __future__ import annotations

import json
import os
from pathlib import Path


class ExecutionEventCursorCheckpoint:
    """Durable cursor owned by one Strategy process instance."""

    def __init__(self, path: str | Path, *, instance_id: str) -> None:
        if not instance_id.strip():
            raise ValueError("execution cursor instance_id is required")
        self.path = Path(path)
        self.instance_id = instance_id

    def load(self) -> int:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except json.JSONDecodeError as error:
            raise ValueError(
                f"execution event cursor is invalid: {self.path}"
            ) from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("unsupported execution event cursor checkpoint")
        if value.get("stream_id") != "execution.events":
            raise ValueError("execution event cursor stream identity changed")
        if value.get("instance_id") != self.instance_id:
            raise ValueError("execution event cursor instance identity changed")
        sequence = value.get("sequence")
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError("execution event cursor sequence is invalid")
        return sequence

    def save(self, sequence: int) -> None:
        if sequence < 0:
            raise ValueError("execution event cursor cannot be negative")
        current = self.load()
        if sequence < current:
            raise ValueError(
                f"execution event cursor regressed: current={current}, next={sequence}"
            )
        if sequence == current:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": 1,
                    "stream_id": "execution.events",
                    "instance_id": self.instance_id,
                    "sequence": sequence,
                },
                stream,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)


__all__ = ["ExecutionEventCursorCheckpoint"]
