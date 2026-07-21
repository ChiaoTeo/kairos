from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from kairospy.backtest.feed import MarketSnapshot
from kairospy.trading.identity import InstrumentId
from kairospy.reference.contracts import InstrumentDefinition
from kairospy.trading.product import ListedOptionSpec, OptionRight
from kairospy.reference.access import contract_spec
from kairospy.storage.codec import from_primitive, to_primitive


@dataclass(frozen=True, slots=True)
class RetainedLeg:
    instrument_id: InstrumentId
    selected_on: date
    expiry: date
    target_delta: Decimal


@dataclass(frozen=True, slots=True)
class RetentionManifest:
    schema_version: int
    dataset_id: str
    decisions: tuple[date, ...]
    legs: tuple[RetainedLeg, ...]


class DeltaLegWatchlist:
    def __init__(
        self,
        root: str | Path,
        dataset_id: str,
        *,
        evaluation_time: time = time(15, 30),
        target_deltas: tuple[Decimal, ...] = (Decimal("-0.25"), Decimal("-0.10")),
        retain_until_dte: int = 3,
    ) -> None:
        self.path = Path(root) / dataset_id / "watchlist.json"
        self.dataset_id = dataset_id
        self.evaluation_time = evaluation_time
        self.target_deltas = target_deltas
        self.retain_until_dte = retain_until_dte
        self.manifest = self._load()

    def active_definitions(self, at, definitions: dict[InstrumentId, InstrumentDefinition]) -> tuple[InstrumentDefinition, ...]:
        local_date = at.astimezone(ZoneInfo("America/New_York")).date()
        active_ids = {
            item.instrument_id for item in self.manifest.legs
            if (item.expiry - local_date).days >= self.retain_until_dte
        }
        return tuple(definitions[item] for item in sorted(active_ids, key=lambda value: value.value) if item in definitions)

    def observe(self, market: MarketSnapshot, current_candidates: tuple[InstrumentDefinition, ...]) -> bool:
        local = market.timestamp.astimezone(ZoneInfo("America/New_York"))
        if local.timetz().replace(tzinfo=None) < self.evaluation_time or local.date() in self.manifest.decisions:
            return False
        snapshots = {item.instrument_id: item for item in market.instruments}
        candidates = []
        for definition in current_candidates:
            spec = contract_spec(definition)
            snapshot = snapshots.get(definition.instrument_id)
            if not isinstance(spec, ListedOptionSpec) or spec.right is not OptionRight.PUT:
                continue
            if snapshot is None or snapshot.greeks is None or snapshot.greeks.delta is None:
                continue
            candidates.append((definition, snapshot.greeks.delta))
        if not candidates:
            return False
        expiry = min(contract_spec(item).expiry for item, _ in candidates)
        same_expiry = [(item, delta) for item, delta in candidates if contract_spec(item).expiry == expiry]
        selected = []
        for target in self.target_deltas:
            definition, _ = min(same_expiry, key=lambda pair: abs(pair[1] - target))
            selected.append((definition, target))
        if len({item.instrument_id for item, _ in selected}) != len(selected):
            return False
        legs = list(self.manifest.legs)
        known = {item.instrument_id for item in legs}
        for definition, target in selected:
            if definition.instrument_id not in known:
                legs.append(RetainedLeg(definition.instrument_id, local.date(), expiry.date(), target))
        self.manifest = RetentionManifest(
            1, self.dataset_id, tuple(sorted((*self.manifest.decisions, local.date()))),
            tuple(sorted(legs, key=lambda item: (item.expiry, item.instrument_id.value))),
        )
        self._save()
        return True

    def _load(self) -> RetentionManifest:
        if not self.path.exists():
            return RetentionManifest(1, self.dataset_id, (), ())
        manifest = from_primitive(json.loads(self.path.read_text(encoding="utf-8")), RetentionManifest)
        if manifest.schema_version != 1 or manifest.dataset_id != self.dataset_id:
            raise ValueError("unsupported or mismatched retention manifest")
        return manifest

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(to_primitive(self.manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
