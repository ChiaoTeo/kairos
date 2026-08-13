"""Reference runtime acceptance checks exposed through the application facade."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from kairospy.infrastructure.contracts.reference_client import ReferenceClient


PUBLIC_REFERENCE_SOURCES = (
    "binance-spot",
    "binance-usdm-futures",
    "binance-coinm-futures",
    "binance-options",
    "okx-spot",
    "okx-swap",
    "okx-futures",
    "okx-options",
    "hyperliquid",
)

MASSIVE_REFERENCE_SOURCES = ("massive-equity", "massive-options")


def validate_reference_runtime(
    client: ReferenceClient,
    *,
    required_sources: Iterable[str] | None = None,
    require_published: bool = True,
) -> dict[str, Any]:
    """Validate the running process, snapshots, durable tail, and providers."""
    health = client.health()
    views = client.reference_views()
    snapshot = client.catalog()
    event_sequence = _integer(health.get("event_sequence"))
    tail = (
        client.events(sequence_from=event_sequence, limit=1)
        if event_sequence > 0
        else {
            "generation": snapshot.get("generation"),
            "event_sequence": 0,
            "events": [],
        }
    )
    provider_rows = health.get("providers")
    provider_by_id = (
        {
            str(row.get("source_id")): row
            for row in provider_rows
            if isinstance(row, dict) and row.get("source_id") is not None
        }
        if isinstance(provider_rows, list)
        else {}
    )
    required = tuple(
        dict.fromkeys(
            str(value)
            for value in (
                provider_by_id.keys() if required_sources is None else required_sources
            )
        )
    )

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: object) -> None:
        checks.append(
            {"name": name, "status": "passed" if passed else "failed", "detail": detail}
        )

    check("process_ready", health.get("status") == "ready", health.get("status"))
    missing = [source for source in required if source not in provider_by_id]
    unhealthy = [
        source
        for source in required
        if source in provider_by_id
        and (
            provider_by_id[source].get("status") != "ready"
            or bool(provider_by_id[source].get("stale"))
        )
    ]
    check(
        "required_providers_ready",
        not missing and not unhealthy,
        {"required": list(required), "missing": missing, "unhealthy": unhealthy},
    )
    missing_views = [str(view.get("view")) for view in views if not view.get("exists")]
    check(
        "reference_views_complete",
        len(views) == 8 and not missing_views,
        {"view_count": len(views), "missing": missing_views},
    )
    health_generation = _integer(health.get("generation"))
    snapshot_generation = _integer(snapshot.get("generation"))
    snapshot_sequence = _integer(snapshot.get("event_sequence"))
    check(
        "snapshot_watermark_matches_health",
        health_generation == snapshot_generation
        and event_sequence == snapshot_sequence,
        {
            "health_generation": health_generation,
            "snapshot_generation": snapshot_generation,
            "health_event_sequence": event_sequence,
            "snapshot_event_sequence": snapshot_sequence,
        },
    )
    catalog_value = snapshot.get("catalog")
    catalog: dict[str, Any] = catalog_value if isinstance(catalog_value, dict) else {}
    health_market_count = _integer(health.get("market_count"))
    snapshot_market_count = _integer(catalog.get("market_count"))
    check(
        "catalog_is_non_empty",
        health_market_count > 0 and health_market_count == snapshot_market_count,
        {
            "health_market_count": health_market_count,
            "snapshot_market_count": snapshot_market_count,
        },
    )
    outbox_depth = _integer(health.get("outbox_depth"))
    check(
        "publication_outbox_drained",
        not require_published or outbox_depth == 0,
        {"required": require_published, "outbox_depth": outbox_depth},
    )
    tail_events = tail.get("events") if isinstance(tail.get("events"), list) else []
    expected_event_id = f"reference:{event_sequence:020}"
    actual_event_id = (
        tail_events[0].get("event_id")
        if tail_events and isinstance(tail_events[0], dict)
        else None
    )
    check(
        "durable_event_tail_matches_watermark",
        event_sequence == 0
        or (
            _integer(tail.get("generation")) == health_generation
            and _integer(tail.get("event_sequence")) == event_sequence
            and actual_event_id == expected_event_id
        ),
        {"expected_event_id": expected_event_id, "actual_event_id": actual_event_id},
    )
    failed = [value["name"] for value in checks if value["status"] == "failed"]
    return {
        "status": "passed" if not failed else "failed",
        "generation": health_generation,
        "event_sequence": event_sequence,
        "market_count": health_market_count,
        "failed_checks": failed,
        "checks": checks,
    }


def _integer(value: object) -> int:
    return int(value) if isinstance(value, int | str) else 0


__all__ = [
    "MASSIVE_REFERENCE_SOURCES",
    "PUBLIC_REFERENCE_SOURCES",
    "validate_reference_runtime",
]
