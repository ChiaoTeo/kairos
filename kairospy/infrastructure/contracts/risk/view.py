"""Risk v2 current-view contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, cast

from kairospy.application.risk.models import RiskStatus
from kairospy.domain_types import AccountId
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


@dataclass(frozen=True, slots=True)
class RiskViewKey:
    actor_id: str = "risk"

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("Risk view actor_id is required")

    def canonical_key(self) -> str:
        return "risk.latest"

    def resource_id(self) -> str:
        return "risk.latest"

    def resource_path(self, root: str | Path) -> Path:
        # Rust's RiskViewKey uses the actor id directly as the resource
        # component; keep the Python path byte-for-byte identical.
        return Path(root) / "risk" / self.actor_id / "latest" / "current.snapshot"


@dataclass(frozen=True, slots=True)
class RiskViewFrame:
    key: RiskViewKey
    generation: int
    payload: bytes
    value: Any


class RiskViewReader:
    """Read the RiskLatestView root from a KSS1 snapshot."""

    def __init__(self, root: str | Path, key: RiskViewKey, *, retries: int = 8) -> None:
        self.root = Path(root)
        self.key = key
        self._reader = SharedSnapshotReader(key.resource_path(self.root), retries=retries)

    @property
    def path(self) -> Path:
        return self._reader.path

    def read(self) -> RiskViewFrame:
        snapshot = self._reader.read()
        value = decode_view(snapshot.payload)
        metadata = value.Metadata()
        if metadata is None:
            raise ValueError("Risk latest view metadata is missing")
        if _text(metadata.ViewKey()) != self.key.canonical_key():
            raise ValueError("Risk latest view key identity mismatch")
        if int(metadata.ResourceEpoch()) != 0:
            raise ValueError("unsupported Risk view resource epoch")
        return RiskViewFrame(
            key=self.key,
            generation=snapshot.generation,
            payload=snapshot.payload,
            value=value,
        )


class RiskProjection:
    """Application projection backed by one v2 Risk latest view."""

    def __init__(self, root: str | Path, key: RiskViewKey, *, retries: int = 8) -> None:
        self._reader = RiskViewReader(root, key, retries=retries)

    @property
    def path(self) -> Path:
        return self._reader.path

    def status(self, account_id: AccountId) -> RiskStatus:
        from kairospy.application.risk import RiskStatus, RiskViolation

        frame = self._reader.read()
        root = cast(Any, frame.value)
        state = root.State()
        if state is None:
            raise ValueError("Risk latest view state is missing")

        limits = tuple(
            value
            for value in _table_items(state, "Limits")
            if _scope_account(value.Policy()) in {None, str(account_id)}
            and _metric(value.Policy()) == _METRIC_NOTIONAL
        )
        circuits = tuple(
            value
            for value in _table_items(state, "Circuits")
            if _scope_account(value.Scope()) in {None, str(account_id)}
        )
        available = sum(
            (_decimal64(value.Available()) or Decimal("0") for value in limits),
            Decimal("0"),
        )
        reserved = sum(
            (_decimal64(value.Reserved()) or Decimal("0") for value in limits),
            Decimal("0"),
        )
        total = sum(
            (
                (_decimal64(value.Used()) or Decimal("0"))
                + (_decimal64(value.Reserved()) or Decimal("0"))
                + (_decimal64(value.Available()) or Decimal("0"))
                for value in limits
            ),
            Decimal("0"),
        )
        violations = tuple(
            RiskViolation(
                code="budget_unavailable",
                message=f"Risk policy {_policy_id(value.Policy())} is not available",
                limit=_decimal64(value.Policy().Limit()),
                actual=(_decimal64(value.Used()) or Decimal("0"))
                + (_decimal64(value.Reserved()) or Decimal("0")),
            )
            for value in limits
            if (_decimal64(value.Available()) or Decimal("0")) < 0
        ) + tuple(
            RiskViolation(
                code="circuit_open",
                message=_text(value.Reason()) or "Risk circuit is open",
            )
            for value in circuits
            if int(value.Status()) == _CIRCUIT_OPEN
        )
        return RiskStatus(
            account_id=account_id,
            trading_allowed=not violations,
            available_notional=available if limits else None,
            reserved_notional=reserved,
            utilization=None if total == 0 else (total - available) / total,
            violations=violations,
            generation=frame.generation,
        )


def decode_view(payload: bytes) -> Any:
    """Decode one Risk v2 current view without copying FlatBuffers tables."""

    from kairospy.infrastructure.transport.generated.kairos.risk.v2.RiskLatestView import (
        RiskLatestView,
    )

    if not RiskLatestView.RiskLatestViewBufferHasIdentifier(payload, 0):
        raise ValueError("invalid Risk latest view identifier: expected b'RXV2'")
    return RiskLatestView.GetRootAs(payload, 0)


_METRIC_NOTIONAL = 1
_CIRCUIT_OPEN = 2


def _table_items(value: object, name: str) -> tuple[Any, ...]:
    table = cast(Any, value)
    result = tuple(
        getattr(table, name)(index)
        for index in range(int(getattr(table, f"{name}Length")()))
    )
    if any(item is None for item in result):
        raise ValueError(f"Risk view contains an empty {name} entry")
    return result


def _scope_account(scope: object | None) -> str | None:
    if scope is None:
        return None
    return _text(cast(Any, scope).AccountId())


def _metric(policy: object | None) -> int:
    return 0 if policy is None else int(cast(Any, policy).Metric())


def _policy_id(policy: object | None) -> str:
    return _text(None if policy is None else cast(Any, policy).PolicyId()) or "unknown"


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = cast(Any, value)
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))


__all__ = [
    "RiskProjection",
    "RiskViewFrame",
    "RiskViewKey",
    "RiskViewReader",
    "decode_view",
]
