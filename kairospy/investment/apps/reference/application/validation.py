"""Reference runtime acceptance checks exposed through the application facade."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


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
    client: Any,
    *,
    required_sources: Iterable[str] | None = None,
    require_published: bool = True,
) -> dict[str, Any]:
    """Validate the running process, current catalog, publication, and providers."""
    health = client.health()
    provider_status = client.providers()
    snapshot = client.catalog()
    event_sequence = _integer(provider_status.get("event_sequence"))
    provider_rows = provider_status.get("providers")
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

    check("process_ready", health.status == "ready", health.status)
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
    durable_generation = _integer(provider_status.get("generation"))
    snapshot_generation = int(snapshot.generation)
    snapshot_sequence = int(snapshot.event_sequence)
    check(
        "snapshot_watermark_matches_health",
        durable_generation == snapshot_generation
        and event_sequence == snapshot_sequence,
        {
            "durable_generation": durable_generation,
            "snapshot_generation": snapshot_generation,
            "durable_event_sequence": event_sequence,
            "snapshot_event_sequence": snapshot_sequence,
        },
    )
    snapshot_market_count = snapshot.catalog.market_count
    check(
        "catalog_is_non_empty",
        snapshot_market_count > 0,
        {"snapshot_market_count": snapshot_market_count},
    )
    outbox_depth = _integer(provider_status.get("outbox_depth"))
    check(
        "publication_outbox_drained",
        not require_published or outbox_depth == 0,
        {"required": require_published, "outbox_depth": outbox_depth},
    )
    failed = [value["name"] for value in checks if value["status"] == "failed"]
    return {
        "status": "passed" if not failed else "failed",
        "generation": durable_generation,
        "event_sequence": event_sequence,
        "market_count": snapshot_market_count,
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
