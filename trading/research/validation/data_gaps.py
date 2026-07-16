from __future__ import annotations

from .models import DataGap, DataGapPlan, ValidationLevel


_REMEDIATION = {
    "synchronous_quotes": ("synchronization", "capture point-in-time bid/ask snapshots"),
    "synchronous_multi_leg_quotes": ("synchronization", "capture all strategy legs at a common as-of time"),
    "top_of_book": ("fields", "capture best bid and ask"), "quote_size": ("fields", "capture executable bid and ask size"),
    "multi_level_order_book": ("granularity", "capture multiple order-book levels"),
    "incremental_order_book": ("granularity", "capture incremental order-book events"),
    "sequence_numbers": ("events", "persist venue sequence numbers and gap recovery"),
    "trade_events": ("events", "capture timestamped trade events"),
    "queue_reconstructable": ("fields", "capture book and trade events sufficient for queue reconstruction"),
    "funding": ("events", "collect funding rates and payments"), "settlement_price": ("events", "collect official settlement values"),
    "derivative_lifecycle_events": ("events", "collect expiry, delivery, and settlement events"),
    "option_lifecycle_events": ("events", "collect expiry, exercise, assignment, and settlement events"),
    "point_in_time_universe": ("coverage", "store point-in-time listings and delistings"),
    "point_in_time_contract_universe": ("coverage", "store historical contract listings and expiries"),
    "point_in_time_option_universe": ("coverage", "store historical option-chain membership"),
}


def build_data_gap_plan(missing_capabilities: tuple[str, ...], *, target_samples: int | None = None,
                        collection_frequency: str | None = None, collection_started_at: str | None = None) -> DataGapPlan:
    gaps=[]
    for capability in dict.fromkeys(missing_capabilities):
        category,remediation=_REMEDIATION.get(capability,("fields",f"collect or derive {capability}"))
        gaps.append(DataGap(category,capability,ValidationLevel.L4_EXECUTABLE,remediation))
    condition=f"reevaluate after {target_samples} effective samples" if target_samples else "reevaluate when all capabilities are available"
    return DataGapPlan(tuple(gaps),collection_frequency,collection_started_at,target_samples,condition)
