"""System-owned telemetry facade for executable entry points."""

from kairospy.infrastructure.observability import (
    configure_from_environment,
    record_counter,
    record_gauge,
    start_span,
)

__all__ = [
    "configure_from_environment",
    "record_counter",
    "record_gauge",
    "start_span",
]
