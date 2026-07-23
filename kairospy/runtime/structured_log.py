from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from kairospy.infrastructure.storage.codec import to_primitive


class StructuredRuntimeLog:
    """Append-only JSONL runtime log for operator-facing daemon events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        event: str,
        *,
        run_id: str,
        level: str = "info",
        payload: Any | None = None,
        at: datetime | None = None,
    ) -> dict[str, object]:
        observed_at = at or datetime.now(timezone.utc)
        row = {
            "schema_version": 1,
            "timestamp": observed_at.isoformat(),
            "run_id": run_id,
            "level": str(level),
            "event": str(event),
            "payload": to_primitive(payload or {}),
        }
        row["record_hash"] = sha256(json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")).hexdigest()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return row

    def read(self) -> tuple[dict[str, object], ...]:
        if not self.path.exists():
            return ()
        return tuple(json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line)
