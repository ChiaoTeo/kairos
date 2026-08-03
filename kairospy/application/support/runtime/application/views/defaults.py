from __future__ import annotations

from kairospy.domain.views import ViewFieldSchema, ViewSchema

from .registry import ViewRegistry


def default_view_registry() -> ViewRegistry:
    return ViewRegistry(
        (
            ViewSchema(
                "system.strategy",
                "system",
                fields=(
                    ViewFieldSchema("strategy_id", "strategy identity", "launch identity", "runtime"),
                    ViewFieldSchema("event_count", "consumed runtime event count", "runtime sequence", "runtime"),
                    ViewFieldSchema("runtime_event_count", "consumed runtime event count", "runtime sequence", "runtime"),
                    ViewFieldSchema("last_event_time", "latest runtime event time", "event time", "runtime event source"),
                    ViewFieldSchema("last_domain", "latest runtime event domain", "event time", "runtime event source"),
                    ViewFieldSchema("last_kind", "latest runtime event kind", "event time", "runtime event source"),
                    ViewFieldSchema("status", "runtime status", "runtime time", "runtime"),
                ),
                mutability="runtime_writable",
                evidence="strategy runtime loop view state",
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
                mutability="runtime_writable",
                evidence="intent journal view state",
            ),
        )
    )


__all__ = ["default_view_registry"]
