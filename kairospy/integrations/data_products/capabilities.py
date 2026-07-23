from __future__ import annotations

from kairospy.data.contracts import DataProductContract


def capabilities_payload(dataset: DataProductContract, release_id: str) -> dict[str, object]:
    return {"capability_schema_version": 2, "dataset_id": release_id, **dict(dataset.capabilities)}
