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
        return (
            Path(root)
            / "risk"
            / _component(self.actor_id)
            / "latest"
            / "current.snapshot"
        )


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
        self._reader = SharedSnapshotReader(
            key.resource_path(self.root), retries=retries
        )

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


class RiskLatestViewQueries:
    """Application current-view queries backed by one v2 Risk latest view."""

    def __init__(self, root: str | Path, key: RiskViewKey, *, retries: int = 8) -> None:
        self._reader = RiskViewReader(root, key, retries=retries)

    @property
    def path(self) -> Path:
        return self._reader.path

    def read_frame(self) -> RiskViewFrame:
        return self._reader.read()

    def latest(self) -> dict[str, Any]:
        frame = self.read_frame()
        root = cast(Any, frame.value)
        state = root.State()
        if state is None:
            raise ValueError("Risk latest view state is missing")
        limits = tuple(_limit_usage(value) for value in _table_items(state, "Limits"))
        reservations = tuple(
            _reservation(value) for value in _table_items(state, "ActiveReservations")
        )
        circuits = tuple(_circuit(value) for value in _table_items(state, "Circuits"))
        return {
            "actor_id": self._reader.key.actor_id,
            "kind": "latest",
            "generation": frame.generation,
            "path": str(self.path),
            "policy_version": int(state.PolicyVersion()),
            "limits": list(limits),
            "active_reservations": list(reservations),
            "circuits": list(circuits),
            "summary": {
                "limit_count": len(limits),
                "active_reservation_count": len(reservations),
                "open_circuit_count": sum(
                    1 for value in circuits if value["status"] == "open"
                ),
            },
        }

    def limits(self) -> tuple[dict[str, Any], ...]:
        state = self._latest_state()
        return tuple(_limit_usage(value) for value in _table_items(state, "Limits"))

    def active_reservations(self) -> tuple[dict[str, Any], ...]:
        state = self._latest_state()
        return tuple(
            _reservation(value) for value in _table_items(state, "ActiveReservations")
        )

    def circuits(self) -> tuple[dict[str, Any], ...]:
        state = self._latest_state()
        return tuple(_circuit(value) for value in _table_items(state, "Circuits"))

    def _latest_state(self) -> Any:
        frame = self.read_frame()
        root = cast(Any, frame.value)
        state = root.State()
        if state is None:
            raise ValueError("Risk latest view state is missing")
        return state

    def status(self, account_id: AccountId) -> RiskStatus:
        from kairospy.application.risk import RiskStatus, RiskViolation

        frame = self.read_frame()
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

_METRICS = {
    0: "unspecified",
    1: "notional",
    2: "margin",
    3: "gross_exposure",
    4: "net_exposure",
    5: "turnover",
    6: "order_rate",
    7: "daily_loss",
    8: "drawdown",
    9: "leverage",
    10: "price_deviation",
    11: "stress_loss",
}

_ENFORCEMENT = {
    0: "unspecified",
    1: "reject",
    2: "warn",
    3: "observe",
}

_RESERVATION_STATUS = {
    0: "unspecified",
    1: "reserved",
    2: "consumed",
    3: "released",
    4: "expired",
}

_CIRCUIT_STATUS = {
    0: "unspecified",
    1: "closed",
    2: "open",
}


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


def _limit_usage(value: object) -> dict[str, Any]:
    row = cast(Any, value)
    policy = row.Policy()
    if policy is None:
        raise ValueError("Risk limit usage policy is missing")
    return {
        "policy": _policy(policy),
        "used": _decimal_text(row.Used()),
        "reserved": _decimal_text(row.Reserved()),
        "available": _decimal_text(row.Available()),
    }


def _policy(policy: object) -> dict[str, Any]:
    row = cast(Any, policy)
    return {
        "policy_id": _text(row.PolicyId()),
        "version": int(row.Version()),
        "scope": _policy_scope(row.Scope()),
        "metric": _enum_name(_METRICS, int(row.Metric())),
        "limit": _decimal_text(row.Limit()),
        "enforcement": _enum_name(_ENFORCEMENT, int(row.Enforcement())),
        "valid_from_unix_nanos": int(row.ValidFromUnixNanos()),
        "valid_until_unix_nanos": _optional_int(row.ValidUntilUnixNanos()),
        "window_nanos": _optional_int(row.WindowNanos()),
    }


def _reservation(value: object) -> dict[str, Any]:
    row = cast(Any, value)
    return {
        "reservation_id": _text(row.ReservationId()),
        "request_id": _text(row.RequestId()),
        "account_id": _text(row.AccountId()),
        "strategy_id": _text(row.StrategyId()),
        "instrument_id": _text(row.InstrumentId()),
        "idempotency_key": _text(row.IdempotencyKey()),
        "requested_usages": [
            _risk_usage(row.RequestedUsages(index))
            for index in range(int(row.RequestedUsagesLength()))
        ],
        "allocations": [
            _allocation(row.Allocations(index))
            for index in range(int(row.AllocationsLength()))
        ],
        "status": _enum_name(_RESERVATION_STATUS, int(row.Status())),
        "created_at_unix_nanos": int(row.CreatedAtUnixNanos()),
        "updated_at_unix_nanos": int(row.UpdatedAtUnixNanos()),
        "expires_at_unix_nanos": int(row.ExpiresAtUnixNanos()),
        "policy_version": int(row.PolicyVersion()),
    }


def _risk_usage(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Risk reservation contains an empty requested usage")
    row = cast(Any, value)
    return {
        "metric": _enum_name(_METRICS, int(row.Metric())),
        "amount": _decimal_text(row.Amount()),
    }


def _allocation(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Risk reservation contains an empty allocation")
    row = cast(Any, value)
    return {
        "policy_id": _text(row.PolicyId()),
        "metric": _enum_name(_METRICS, int(row.Metric())),
        "amount": _decimal_text(row.Amount()),
    }


def _circuit(value: object) -> dict[str, Any]:
    row = cast(Any, value)
    return {
        "circuit_id": _text(row.CircuitId()),
        "scope": _circuit_scope(row.Scope()),
        "status": _enum_name(_CIRCUIT_STATUS, int(row.Status())),
        "opened_at_unix_nanos": _optional_int(row.OpenedAtUnixNanos()),
        "reset_at_unix_nanos": _optional_int(row.ResetAtUnixNanos()),
        "reason": _text(row.Reason()),
    }


def _policy_scope(scope: object | None) -> dict[str, str | None]:
    if scope is None:
        return {
            "account_id": None,
            "strategy_id": None,
            "instrument_id": None,
            "exchange_id": None,
        }
    row = cast(Any, scope)
    return {
        "account_id": _text(row.AccountId()),
        "strategy_id": _text(row.StrategyId()),
        "instrument_id": _text(row.InstrumentId()),
        "exchange_id": _text(row.ExchangeId()),
    }


def _circuit_scope(scope: object | None) -> dict[str, str | None]:
    if scope is None:
        return {"account_id": None, "strategy_id": None, "exchange_id": None}
    row = cast(Any, scope)
    return {
        "account_id": _text(row.AccountId()),
        "strategy_id": _text(row.StrategyId()),
        "exchange_id": _text(row.ExchangeId()),
    }


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if (byte < 128 and chr(byte).isalnum()) or byte in b"-_."
        else f"%{byte:02X}"
        for byte in value.encode("utf-8")
    )


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = cast(Any, value)
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))


def _decimal_text(value: object | None) -> str | None:
    decimal = _decimal64(value)
    return None if decimal is None else str(decimal)


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(value)


def _enum_name(mapping: dict[int, str], value: int) -> str:
    return mapping.get(value, f"unknown:{value}")


__all__ = [
    "RiskLatestViewQueries",
    "RiskViewFrame",
    "RiskViewKey",
    "RiskViewReader",
    "decode_view",
]
