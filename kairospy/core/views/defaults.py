from __future__ import annotations

from .registry import ViewRegistry
from .schema import ViewFieldSchema, ViewSchema


def default_view_registry() -> ViewRegistry:
    return ViewRegistry(
        (
            ViewSchema(
                "system.strategy",
                "system",
                fields=(
                    ViewFieldSchema("strategy_id", "strategy identity", "run identity", "runtime"),
                    ViewFieldSchema("event_count", "consumed market event count", "runtime sequence", "runtime"),
                    ViewFieldSchema("runtime_event_count", "consumed runtime event count", "runtime sequence", "runtime"),
                    ViewFieldSchema("last_event_time", "latest market event time", "event time", "market event source"),
                    ViewFieldSchema("last_stream", "latest market event stream", "event time", "market event source"),
                    ViewFieldSchema("last_runtime_event_time", "latest runtime event time", "event time", "runtime event source"),
                    ViewFieldSchema("last_runtime_stream", "latest runtime event stream", "event time", "runtime event source"),
                    ViewFieldSchema("status", "runtime status", "runtime time", "runtime"),
                ),
                evidence="strategy runtime loop",
            ),
            ViewSchema(
                "system.data",
                "system",
                fields=(
                    ViewFieldSchema("bindings", "data bindings", "binding time", "market data service"),
                ),
                evidence="market data service snapshot",
            ),
            ViewSchema(
                "system.intents",
                "system",
                fields=(
                    ViewFieldSchema("total_count", "known strategy intent count", "runtime state", "IntentJournal"),
                    ViewFieldSchema("active_count", "active strategy intent count", "runtime state", "IntentJournal"),
                    ViewFieldSchema("states", "strategy intent state summaries", "runtime state", "IntentJournal"),
                ),
                evidence="intent journal projection",
            ),
            ViewSchema(
                "system.control",
                "system",
                fields=(
                    ViewFieldSchema("total_count", "control request count", "runtime state", "ControlJournal"),
                    ViewFieldSchema("requests", "strategy runtime control requests", "request time", "ControlJournal"),
                ),
                evidence="control request journal projection",
            ),
        )
    )


__all__ = ["default_view_registry"]
