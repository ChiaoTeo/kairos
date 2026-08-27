"""Risk owner-scoped LMDB indexed current-view contract."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, cast

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.indexed_view import (
    IndexedViewMetadata,
    IndexedViewReader,
    IndexedViewSchema,
)
from kairospy.primitives.account import AccountId

sys.modules.setdefault("kairos", _generated_kairos)

STATE_DATABASE = "state"
POLICIES_DATABASE = "policies"
LIMIT_USAGE_DATABASE = "limit_usage"
ALLOCATIONS_DATABASE = "allocations"
RESERVATIONS_DATABASE = "reservations"
CIRCUITS_DATABASE = "circuits"
RISK_RESOURCE_EPOCH = 1
RISK_MAP_SIZE = 128 * 1024 * 1024
_KEY_VERSION = 1
_ALL_VALUES_PREFIX = bytes((_KEY_VERSION,))
MAX_INDEXED_VALUES_PER_DATABASE = 100_000

RISK_INDEXED_SCHEMAS = (
    IndexedViewSchema(STATE_DATABASE, 1, "RSM3", 1),
    IndexedViewSchema(POLICIES_DATABASE, 1, "RPO3", 1),
    IndexedViewSchema(LIMIT_USAGE_DATABASE, 1, "RLU3", 1),
    IndexedViewSchema(ALLOCATIONS_DATABASE, 1, "RAL3", 1),
    IndexedViewSchema(RESERVATIONS_DATABASE, 1, "RRS3", 1),
    IndexedViewSchema(CIRCUITS_DATABASE, 1, "RCI3", 1),
)

_ROOTS: dict[str, tuple[bytes, str]] = {
    STATE_DATABASE: (b"RSM3", "RiskStateCurrent"),
    POLICIES_DATABASE: (b"RPO3", "RiskPolicyCurrent"),
    LIMIT_USAGE_DATABASE: (b"RLU3", "RiskLimitUsageCurrent"),
    ALLOCATIONS_DATABASE: (b"RAL3", "RiskAllocationCurrent"),
    RESERVATIONS_DATABASE: (b"RRS3", "RiskReservationCurrent"),
    CIRCUITS_DATABASE: (b"RCI3", "RiskCircuitCurrent"),
}


def risk_indexed_environment_path(root: str | Path, actor_id: str) -> Path:
    return (
        Path(root)
        / "views"
        / "v3"
        / "Risk"
        / f"risk-{_component(actor_id)}"
        / "epoch-1"
        / "current.lmdb"
    )


def risk_indexed_key(*parts: str) -> bytes:
    if not parts:
        raise ValueError("Risk indexed key requires at least one component")
    key = bytearray((_KEY_VERSION,))
    for part in parts:
        encoded = part.encode()
        if not part or part.strip() != part or b"\0" in encoded:
            raise ValueError(
                "Risk indexed key components must be non-empty and trimmed"
            )
        if len(encoded) > 0xFFFF:
            raise ValueError("Risk indexed key component is too long")
        key.extend(len(encoded).to_bytes(2, "big"))
        key.extend(encoded)
    return bytes(key)


def _key_parts(key: bytes) -> tuple[str, ...]:
    if not key or key[0] != _KEY_VERSION:
        raise ValueError("invalid Risk indexed key version")
    offset = 1
    parts: list[str] = []
    while offset < len(key):
        if offset + 2 > len(key):
            raise ValueError("truncated Risk indexed key")
        length = int.from_bytes(key[offset : offset + 2], "big")
        offset += 2
        if offset + length > len(key):
            raise ValueError("truncated Risk indexed key")
        parts.append(key[offset : offset + length].decode())
        offset += length
    return tuple(parts)


def _decode(payload: bytes, database: str) -> Any:
    identifier, root_name = _ROOTS[database]
    if len(payload) < 8 or payload[4:8] != identifier:
        raise ValueError(f"invalid Risk indexed value identifier for {database}")
    module = __import__(
        f"kairospy.infrastructure.protocol.generated.kairos.risk.v2.{root_name}",
        fromlist=[root_name],
    )
    return getattr(module, root_name).GetRootAs(payload, 0)


class RiskIndexedViewQueries:
    """Explicit bounded snapshots over Risk indexed families."""

    def __init__(
        self,
        root: str | Path,
        *,
        actor_id: str,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        if not actor_id or actor_id.strip() != actor_id:
            raise ValueError("Risk actor_id is required and must be trimmed")
        self.actor_id = actor_id
        self._reader = IndexedViewReader(
            risk_indexed_environment_path(root, actor_id),
            map_size=RISK_MAP_SIZE,
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
            owner="Risk",
            publisher_resource_id=f"risk-{actor_id}",
            resource_epoch=RISK_RESOURCE_EPOCH,
            schemas=RISK_INDEXED_SCHEMAS,
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def _snapshot(self) -> tuple[IndexedViewMetadata, dict[str, tuple[Any, ...]]]:
        snapshot = self._reader.snapshot(
            tuple(
                (
                    database,
                    _ALL_VALUES_PREFIX,
                    MAX_INDEXED_VALUES_PER_DATABASE + 1,
                )
                for database in _ROOTS
            )
        )
        if snapshot.metadata.rebuild_state != "ready":
            raise RuntimeError("Risk indexed current view is not ready")
        decoded: dict[str, tuple[Any, ...]] = {}
        for database, rows in snapshot.rows.items():
            if len(rows) > MAX_INDEXED_VALUES_PER_DATABASE:
                raise RuntimeError(
                    f"Risk indexed database {database} exceeds its read bound"
                )
            values = []
            for key, payload in rows:
                value = _decode(payload, database)
                self._validate(database, _key_parts(key), value)
                values.append(value)
            decoded[database] = tuple(values)
        return snapshot.metadata, decoded

    def _validate(self, database: str, parts: tuple[str, ...], value: Any) -> None:
        if _text(value.ActorId()) != self.actor_id:
            raise ValueError("Risk indexed actor identity mismatch")
        expected = {
            STATE_DATABASE: (self.actor_id,),
            POLICIES_DATABASE: (_text(value.Policy().PolicyId()) or "",),
            LIMIT_USAGE_DATABASE: (_text(value.PolicyId()) or "",),
            ALLOCATIONS_DATABASE: (
                _text(value.ReservationId()) or "",
                _text(value.Allocation().PolicyId()) or "",
                _enum_name(_METRICS, int(value.Allocation().Metric())).upper(),
            ),
            RESERVATIONS_DATABASE: (_text(value.Reservation().ReservationId()) or "",),
            CIRCUITS_DATABASE: (_text(value.CircuitKey()) or "",),
        }[database]
        if parts != expected:
            raise ValueError("Risk indexed key/value identity mismatch")

    def latest(self) -> dict[str, Any]:
        metadata, values = self._snapshot()
        state = _one(values[STATE_DATABASE], "state")
        limits = _limits(values)
        reservations = _reservations(values)
        circuits = tuple(
            _circuit(value.Circuit()) for value in values[CIRCUITS_DATABASE]
        )
        return {
            "actor_id": self.actor_id,
            "kind": "latest",
            "generation": int(state.Generation()),
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
            "applied_event_sequence": metadata.applied_event_sequence,
        }

    def limits(self) -> tuple[dict[str, Any], ...]:
        return _limits(self._snapshot()[1])

    def active_reservations(self) -> tuple[dict[str, Any], ...]:
        return _reservations(self._snapshot()[1])

    def circuits(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _circuit(value.Circuit())
            for value in self._snapshot()[1][CIRCUITS_DATABASE]
        )

    def status(self, account_id: AccountId) -> dict[str, object]:
        metadata, values = self._snapshot()
        policies = {
            _text(value.Policy().PolicyId()): value.Policy()
            for value in values[POLICIES_DATABASE]
        }
        usages = tuple(
            value
            for value in values[LIMIT_USAGE_DATABASE]
            if (policy := policies.get(_text(value.PolicyId()))) is not None
            and _scope_account(policy.Scope()) in {None, str(account_id)}
            and int(policy.Metric()) == _METRIC_NOTIONAL
        )
        circuits = tuple(
            value.Circuit()
            for value in values[CIRCUITS_DATABASE]
            if _scope_account(value.Circuit().Scope()) in {None, str(account_id)}
        )
        available = sum(
            (_decimal64(value.Available()) or Decimal(0) for value in usages),
            Decimal(0),
        )
        reserved = sum(
            (_decimal64(value.Reserved()) or Decimal(0) for value in usages), Decimal(0)
        )
        violations = [
            {
                "code": "circuit_open",
                "message": _text(value.Reason()) or "Risk circuit is open",
                "limit": None,
                "actual": None,
            }
            for value in circuits
            if int(value.Status()) == _CIRCUIT_OPEN
        ]
        return {
            "account_id": str(account_id),
            "trading_allowed": not violations,
            "available_notional": _format_decimal(available) if usages else None,
            "reserved_notional": _format_decimal(reserved),
            "utilization": None,
            "violations": violations,
            "generation": metadata.applied_event_sequence,
        }


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
_ENFORCEMENT = {0: "unspecified", 1: "reject", 2: "warn", 3: "observe"}
_RESERVATION_STATUS = {
    0: "unspecified",
    1: "reserved",
    2: "consumed",
    3: "released",
    4: "expired",
}
_CIRCUIT_STATUS = {0: "unspecified", 1: "closed", 2: "open"}


def _limits(values: dict[str, tuple[Any, ...]]) -> tuple[dict[str, Any], ...]:
    policies = {
        _text(value.Policy().PolicyId()): value.Policy()
        for value in values[POLICIES_DATABASE]
    }
    return tuple(
        {
            "policy": _policy(policies[_text(value.PolicyId())]),
            "used": _decimal_text(value.Used()),
            "reserved": _decimal_text(value.Reserved()),
            "available": _decimal_text(value.Available()),
        }
        for value in values[LIMIT_USAGE_DATABASE]
    )


def _reservations(values: dict[str, tuple[Any, ...]]) -> tuple[dict[str, Any], ...]:
    allocations: dict[str, list[dict[str, Any]]] = {}
    for value in values[ALLOCATIONS_DATABASE]:
        allocations.setdefault(_text(value.ReservationId()) or "", []).append(
            _allocation(value.Allocation())
        )
    return tuple(
        _reservation(
            value.Reservation(),
            allocations.get(_text(value.Reservation().ReservationId()) or "", []),
        )
        for value in values[RESERVATIONS_DATABASE]
    )


def _policy(row: Any) -> dict[str, Any]:
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


def _reservation(row: Any, allocations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "reservation_id": _text(row.ReservationId()),
        "request_id": _text(row.RequestId()),
        "account_id": _text(row.AccountId()),
        "strategy_id": _text(row.StrategyId()),
        "instrument_id": "",
        "idempotency_key": _text(row.IdempotencyKey()),
        "requested_usages": [],
        "allocations": allocations,
        "status": _enum_name(_RESERVATION_STATUS, int(row.Status())),
        "created_at_unix_nanos": int(row.CreatedAtUnixNanos()),
        "updated_at_unix_nanos": int(row.UpdatedAtUnixNanos()),
        "expires_at_unix_nanos": int(row.ExpiresAtUnixNanos()),
        "policy_version": int(row.PolicyVersion()),
    }


def _allocation(row: Any) -> dict[str, Any]:
    return {
        "policy_id": _text(row.PolicyId()),
        "metric": _enum_name(_METRICS, int(row.Metric())),
        "amount": _decimal_text(row.Amount()),
    }


def _circuit(row: Any) -> dict[str, Any]:
    return {
        "circuit_id": _text(row.CircuitId()),
        "scope": _circuit_scope(row.Scope()),
        "status": _enum_name(_CIRCUIT_STATUS, int(row.Status())),
        "opened_at_unix_nanos": _optional_int(row.OpenedAtUnixNanos()),
        "reset_at_unix_nanos": _optional_int(row.ResetAtUnixNanos()),
        "reason": _text(row.Reason()),
    }


def _policy_scope(row: Any | None) -> dict[str, str | None]:
    return (
        {
            "account_id": None,
            "strategy_id": None,
            "instrument_id": None,
            "exchange_id": None,
        }
        if row is None
        else {
            "account_id": _text(row.AccountId()),
            "strategy_id": _text(row.StrategyId()),
            "instrument_id": _text(row.InstrumentId()),
            "exchange_id": _text(row.ExchangeId()),
        }
    )


def _circuit_scope(row: Any | None) -> dict[str, str | None]:
    return (
        {"account_id": None, "strategy_id": None, "exchange_id": None}
        if row is None
        else {
            "account_id": _text(row.AccountId()),
            "strategy_id": _text(row.StrategyId()),
            "exchange_id": _text(row.ExchangeId()),
        }
    )


def _scope_account(row: Any | None) -> str | None:
    return None if row is None else _text(row.AccountId())


def _one(values: tuple[Any, ...], name: str) -> Any:
    if len(values) != 1:
        raise ValueError(f"Risk indexed snapshot must contain exactly one {name}")
    return values[0]


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if byte < 128 and (chr(byte).isalnum() or chr(byte) in "-_ .".replace(" ", ""))
        else f"%{byte:02X}"
        for byte in value.encode()
    )


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = cast(Any, value)
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))


def _decimal_text(value: object | None) -> str | None:
    decimal = _decimal64(value)
    return None if decimal is None else str(decimal)


def _format_decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(cast(Any, value))


def _enum_name(mapping: dict[int, str], value: int) -> str:
    return mapping.get(value, f"unknown:{value}")


__all__ = [
    "RiskIndexedViewQueries",
    "risk_indexed_environment_path",
    "risk_indexed_key",
]
