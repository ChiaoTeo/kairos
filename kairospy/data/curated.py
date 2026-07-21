from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from kairospy.configuration import DEFAULT_LAKE_ROOT

from .client import DatasetClient
from .contracts import DatasetKey, DatasetLayer, DatasetLike, DataProductDefinition, QualityLevel
from .products import DataProductContract
from .publishing import content_release_id, publish_release, release_path
from kairospy.storage.data_lake import write_event_dataset


@dataclass(frozen=True, slots=True)
class ConsolidatedTradeInput:
    dataset: DatasetLike
    provider: str
    venue: str
    instrument_type: str
    quote_currency: str
    price_field: str = "price"
    size_field: str = "size"


@dataclass(frozen=True, slots=True)
class ConsolidatedTradePolicy:
    policy_id: str
    version: str
    target_currency: str
    fx_to_target: dict[str, Decimal]

    def __post_init__(self) -> None:
        if not self.policy_id or not self.version or not self.target_currency:
            raise ValueError("consolidation policy identity and target currency are required")
        if any(value <= 0 for value in self.fx_to_target.values()):
            raise ValueError("currency conversion rates must be positive")


class ConsolidatedTradeBuilder:
    """Build an explicit cross-venue product; never acts as source fallback."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root, self.data = Path(root), DatasetClient(root)

    def build(self, output_key: DatasetKey | str, title: str, inputs: tuple[ConsolidatedTradeInput, ...],
              policy: ConsolidatedTradePolicy, *, start, end):
        if len(inputs) < 2:
            raise ValueError("cross-venue consolidation requires at least two explicit inputs")
        instrument_types = {item.instrument_type for item in inputs}
        if len(instrument_types) != 1:
            raise ValueError("spot, future, option and perpetual trades cannot be mixed in one consolidated product")
        rows, lineage_inputs = [], []
        for source in inputs:
            rate = policy.fx_to_target.get(source.quote_currency)
            if rate is None:
                raise ValueError(f"no {source.quote_currency}->{policy.target_currency} conversion in policy")
            release = self.data.catalog.release(source.dataset, provider=source.provider, venue=source.venue)
            if release.content_hash is None:
                raise ValueError(f"source release {release.release_id!r} has no content hash")
            lineage_inputs.append({"release_id": release.release_id, "content_hash": release.content_hash,
                                   "provider": source.provider, "venue": source.venue})
            for row in self.data.iter_rows(release.release_id, start=start, end=end):
                if row.get(source.price_field) is None or row.get(source.size_field) is None:
                    continue
                rows.append({
                    "event_time": row["event_time"], "available_time": row.get("available_time", row["event_time"]),
                    "instrument_id": str(row.get("instrument_id", "")), "instrument_type": source.instrument_type,
                    "price": Decimal(str(row[source.price_field])) * rate,
                    "size": Decimal(str(row[source.size_field])), "currency": policy.target_currency,
                    "source_release_id": release.release_id, "provider": source.provider, "venue": source.venue,
                    "source_trade_id": str(row.get("trade_id") or row.get("sequence_number") or ""),
                })
        if not rows:
            raise ValueError("cross-venue consolidation produced no rows")
        rows.sort(key=lambda row: (str(row["available_time"]), row["venue"], row["source_trade_id"]))
        key = output_key if isinstance(output_key, DatasetKey) else DatasetKey(output_key)
        product = DataProductDefinition(
            key, title, DatasetLayer.CURATED,
            dimensions={"data_type": "consolidated_trade", "instrument_type": next(iter(instrument_types)),
                        "currency": policy.target_currency, "venue_scope": "multi"},
        )
        managed = DataProductContract(product, f"curated/consolidated_trades/product={key}",
                                 "curated.consolidated_trade.v1", {"point_in_time_universe": True,
                                 "trade_events": True, "maximum_validation_level": 2})
        material = {"inputs": lineage_inputs, "policy": {"id": policy.policy_id, "version": policy.version,
                    "target_currency": policy.target_currency,
                    "fx_to_target": {name: str(value) for name, value in policy.fx_to_target.items()}}, "rows": rows}
        release_id = content_release_id(managed, material)
        lineage = {"lineage_version": 2, "dataset_id": release_id, "inputs": lineage_inputs,
                   "producer": {"name": type(self).__name__, "transform": policy.policy_id,
                                "version": policy.version}, "parameters": material["policy"],
                   "point_in_time_safe": True, "source_fallback": False}
        schema = {"schema_id": managed.schema_id, "schema_version": 1,
                  "primary_key": ["source_release_id", "venue", "source_trade_id", "event_time"],
                  "columns": {"event_time": {"type": "datetime", "timezone": "UTC"},
                              "available_time": {"type": "datetime", "timezone": "UTC"},
                              "instrument_id": {"type": "string"}, "instrument_type": {"type": "string"},
                              "price": {"type": "decimal", "currency": policy.target_currency},
                              "size": {"type": "decimal"}, "currency": {"type": "string"},
                              "source_release_id": {"type": "string"}, "provider": {"type": "string"},
                              "venue": {"type": "string"}, "source_trade_id": {"type": "string"}}}
        manifest = write_event_dataset(self.root / release_path(managed, release_id), rows, dataset_id=release_id,
                                       schema=schema, lineage=lineage,
                                       capabilities={"capability_schema_version": 2, "dataset_id": release_id,
                                                     **dict(managed.capabilities)})
        return publish_release(self.root, managed, release_id, manifest, provider="internal", venue="multi",
                               transform_id=policy.policy_id, transform_version=policy.version,
                               quality_level=QualityLevel.WORKSPACE)
