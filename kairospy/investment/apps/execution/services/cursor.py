from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExecutionEventCursorPosition:
    producer: str | None
    producer_incarnation: int | None
    sequence: int


class ExecutionEventCursorCheckpoint:
    """Durable cursor owned by one Strategy process instance."""

    def __init__(self, path: str | Path, *, instance_id: str) -> None:
        if not instance_id.strip():
            raise ValueError("execution cursor instance_id is required")
        self.path = Path(path)
        self.instance_id = instance_id

    def load(self) -> int:
        return self.load_position().sequence

    def load_position(self) -> ExecutionEventCursorPosition:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ExecutionEventCursorPosition(None, None, 0)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"execution event cursor is invalid: {self.path}"
            ) from error
        if not isinstance(value, dict) or value.get("schema_version") not in {1, 2}:
            raise ValueError("unsupported execution event cursor checkpoint")
        if value.get("stream_id") != "execution.events":
            raise ValueError("execution event cursor stream identity changed")
        if value.get("instance_id") != self.instance_id:
            raise ValueError("execution event cursor instance identity changed")
        sequence = value.get("sequence")
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError("execution event cursor sequence is invalid")
        if value["schema_version"] == 1:
            return ExecutionEventCursorPosition(None, None, sequence)
        producer = value.get("producer")
        producer_incarnation = value.get("producer_incarnation")
        if not isinstance(producer, str) or not producer.strip():
            raise ValueError("execution event cursor producer is invalid")
        if (
            isinstance(producer_incarnation, bool)
            or not isinstance(producer_incarnation, int)
            or producer_incarnation <= 0
        ):
            raise ValueError("execution event cursor producer incarnation is invalid")
        return ExecutionEventCursorPosition(producer, producer_incarnation, sequence)

    def save(
        self,
        sequence: int,
        *,
        producer: str | None = None,
        producer_incarnation: int | None = None,
    ) -> None:
        if sequence < 0:
            raise ValueError("execution event cursor cannot be negative")
        if (producer is None) != (producer_incarnation is None):
            raise ValueError("execution cursor producer identity must be complete")
        if producer is not None and not producer.strip():
            raise ValueError("execution cursor producer is required")
        if producer_incarnation is not None and producer_incarnation <= 0:
            raise ValueError("execution cursor producer incarnation must be positive")
        current = self.load_position()
        same_producer = (
            current.producer == producer
            and current.producer_incarnation == producer_incarnation
        )
        if same_producer and sequence < current.sequence:
            raise ValueError(
                "execution event cursor regressed: "
                f"current={current.sequence}, next={sequence}"
            )
        if same_producer and sequence == current.sequence:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": 2 if producer is not None else 1,
                    "stream_id": "execution.events",
                    "instance_id": self.instance_id,
                    "sequence": sequence,
                    **({
                        "producer": producer,
                        "producer_incarnation": producer_incarnation,
                    } if producer is not None else {}),
                },
                stream,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)


__all__ = ["ExecutionEventCursorCheckpoint", "ExecutionEventCursorPosition"]
