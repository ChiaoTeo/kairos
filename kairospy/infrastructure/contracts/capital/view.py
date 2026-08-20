"""Capital v2 current-view contract and application projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, cast

from kairospy.application.capital.models import (
    CapitalAvailability,
    CapitalFundingHorizon,
    CapitalReadiness,
    FundingLocation,
)
from kairospy.domain_types import AccountId, SegmentKey
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


@dataclass(frozen=True, slots=True)
class CapitalViewKey:
    capital_group_id: str

    def __post_init__(self) -> None:
        if not self.capital_group_id.strip():
            raise ValueError("Capital view capital_group_id is required")

    def canonical_key(self) -> str:
        return f"capital.current/{_component(self.capital_group_id)}"

    def resource_path(self, root: str | Path) -> Path:
        return (
            Path(root)
            / "capital"
            / _component(self.capital_group_id)
            / "current"
            / "current.snapshot"
        )


@dataclass(frozen=True, slots=True)
class CapitalViewFrame:
    key: CapitalViewKey
    generation: int
    applied_event_sequence: int
    payload: bytes
    value: Any


class CapitalViewReader:
    def __init__(
        self, root: str | Path, key: CapitalViewKey, *, retries: int = 8
    ) -> None:
        self.key = key
        self._reader = SharedSnapshotReader(key.resource_path(root), retries=retries)

    @property
    def path(self) -> Path:
        return self._reader.path

    def read(self) -> CapitalViewFrame:
        snapshot = self._reader.read()
        value = decode_view(snapshot.payload)
        metadata = value.Metadata()
        state = value.State()
        if metadata is None or state is None:
            raise ValueError("Capital current view is incomplete")
        if _text(metadata.ViewKey()) != self.key.canonical_key():
            raise ValueError("Capital current view key identity mismatch")
        if _text(state.CapitalGroupId()) != self.key.capital_group_id:
            raise ValueError("Capital current view group identity mismatch")
        return CapitalViewFrame(
            key=self.key,
            generation=snapshot.generation,
            applied_event_sequence=snapshot.applied_event_sequence,
            payload=snapshot.payload,
            value=value,
        )


class CapitalProjection:
    """Read Capital availability without using its JSON control plane."""

    def __init__(
        self, root: str | Path, capital_group_id: str, *, retries: int = 8
    ) -> None:
        self._reader = CapitalViewReader(
            root, CapitalViewKey(capital_group_id), retries=retries
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def availabilities(self) -> tuple[CapitalAvailability, ...]:
        frame = self._reader.read()
        state = cast(Any, frame.value.State())
        return tuple(
            _availability(state.Availability(index), self._reader.key.capital_group_id)
            for index in range(int(state.AvailabilityLength()))
        )

    def availability(
        self,
        *,
        capital_group_id: str | None,
        location: FundingLocation | None,
    ) -> CapitalAvailability:
        if capital_group_id != self._reader.key.capital_group_id:
            raise ValueError("Capital projection belongs to another capital group")
        values = self.availabilities()
        if location is None:
            if len(values) != 1:
                raise ValueError(
                    "Capital location is required when the group has multiple locations"
                )
            return values[0]
        for value in values:
            if value.location == location:
                return value
        raise LookupError("Capital location has not been evaluated")


def decode_view(payload: bytes) -> Any:
    from kairospy.infrastructure.transport.generated.kairos.capital.v2.CapitalCurrentView import (
        CapitalCurrentView,
    )

    if not CapitalCurrentView.CapitalCurrentViewBufferHasIdentifier(payload, 0):
        raise ValueError("invalid Capital current view identifier: expected b'CPV2'")
    return CapitalCurrentView.GetRootAs(payload, 0)


def _availability(value: object | None, capital_group_id: str) -> CapitalAvailability:
    if value is None:
        raise ValueError("Capital view contains an empty availability entry")
    row = cast(Any, value)
    location = row.Destination()
    if location is None:
        raise ValueError("Capital availability destination is missing")
    readiness = {
        0: CapitalReadiness.WAITING_FOR_FACTS,
        1: CapitalReadiness.DEGRADED,
        2: CapitalReadiness.READY,
    }.get(int(row.Readiness()))
    if readiness is None:
        raise ValueError(f"unknown Capital readiness: {row.Readiness()}")
    return CapitalAvailability(
        capital_group_id=capital_group_id,
        readiness=readiness,
        location=FundingLocation(
            broker=_required_text(location.Broker(), "broker"),
            account_id=AccountId(_required_text(location.AccountId(), "account_id")),
            segment=SegmentKey(_required_text(location.Segment(), "segment")),
            asset=_required_text(location.Asset(), "asset"),
        ),
        policy_version=int(row.PolicyVersion()),
        active_objective_ids=_strings(row, "ActiveObjectiveIds"),
        active_demand_ids=_strings(row, "ActiveDemandIds"),
        funding_horizons=tuple(
            _funding_horizon(row.FundingHorizons(index))
            for index in range(int(row.FundingHorizonsLength()))
        ),
        desired_target=_decimal(row.DesiredTarget()),
        observed_available=_decimal(row.ObservedAvailable()),
        effective_target=_decimal(row.EffectiveTarget()),
        deficit=_decimal(row.Deficit()),
        account_watermark=int(row.AccountWatermark()),
        risk_policy_version=int(row.RiskPolicyVersion()),
        risk_watermark=int(row.RiskWatermark()),
        reason=_text(row.Reason()),
    )


def _funding_horizon(value: object | None) -> CapitalFundingHorizon:
    if value is None:
        raise ValueError("Capital view contains an empty funding horizon")
    row = cast(Any, value)
    return CapitalFundingHorizon(
        required_by=datetime.fromtimestamp(
            int(row.RequiredByUnixNanos()) / 1_000_000_000,
            tz=timezone.utc,
        ),
        objective_ids=_strings(row, "ObjectiveIds"),
        demand_ids=_strings(row, "DemandIds"),
        desired_available=_decimal(row.DesiredAvailable()),
    )


def _strings(value: Any, name: str) -> tuple[str, ...]:
    return tuple(
        _required_text(getattr(value, name)(index), name)
        for index in range(int(getattr(value, f"{name}Length")()))
    )


def _decimal(value: object | None) -> Decimal:
    if value is None:
        raise ValueError("Capital decimal field is missing")
    item = cast(Any, value)
    scale = int(item.Scale())
    if not 0 <= scale <= 18:
        raise ValueError("Capital decimal scale exceeds 18")
    return Decimal(int(item.Mantissa())).scaleb(-scale)


def _required_text(value: bytes | None, name: str) -> str:
    text = _text(value)
    if text is None or not text:
        raise ValueError(f"Capital view field {name} is missing")
    return text


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if (byte < 128 and chr(byte).isalnum()) or byte in b"-_."
        else f"%{byte:02X}"
        for byte in value.encode()
    )


__all__ = [
    "CapitalProjection",
    "CapitalViewFrame",
    "CapitalViewKey",
    "CapitalViewReader",
    "decode_view",
]
