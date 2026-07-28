from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

from .line import RuntimeLine
from ..model import RuntimeDataEnvelope, RuntimeMode, system_data_envelope


def mode_runtime_line(
    mode: RuntimeMode | str,
    events: Iterable[RuntimeDataEnvelope],
    *,
    started_at: datetime | None = None,
    payload: Mapping[str, object] | None = None,
) -> RuntimeLine:
    values = tuple(events)
    runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
    if started_at is None and values:
        started_at = values[0].time
    prefix: tuple[RuntimeDataEnvelope, ...] = ()
    if started_at is not None:
        prefix = (
            system_data_envelope(
                f"runtime.mode.{runtime_mode.value}.started",
                sequence=1,
                time=started_at,
                payload={"mode": runtime_mode.value, **dict(payload or {})},
                stream="system.runtime",
            ),
        )
    return RuntimeLine(runtime_mode, (*prefix, *values))


__all__ = ["mode_runtime_line"]
