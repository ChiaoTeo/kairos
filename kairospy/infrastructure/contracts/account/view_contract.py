"""Account owner-scoped LMDB indexed current-view contract."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.indexed_view import (
    IndexedViewMetadata,
    IndexedViewReader,
    IndexedViewSchema,
)

sys.modules.setdefault("kairos", _generated_kairos)

SEGMENTS_DATABASE = "segments"
BALANCES_DATABASE = "balances"
COLLATERAL_DATABASE = "collateral"
POSITIONS_DATABASE = "positions"
VALUATIONS_DATABASE = "valuations"
EARN_HOLDINGS_DATABASE = "earn_holdings"
OBSERVED_ORDERS_DATABASE = "observed_orders"
ACCOUNT_RESOURCE_EPOCH = 1
ACCOUNT_MAP_SIZE = 256 * 1024 * 1024
_KEY_VERSION = 1
_ALL_VALUES_PREFIX = bytes((_KEY_VERSION,))
MAX_INDEXED_VALUES_PER_DATABASE = 100_000

ACCOUNT_INDEXED_SCHEMAS = (
    IndexedViewSchema(SEGMENTS_DATABASE, 1, "ASG3", 1),
    IndexedViewSchema(BALANCES_DATABASE, 1, "ABA3", 1),
    IndexedViewSchema(COLLATERAL_DATABASE, 1, "ACO3", 1),
    IndexedViewSchema(POSITIONS_DATABASE, 1, "APO3", 1),
    IndexedViewSchema(VALUATIONS_DATABASE, 1, "AVL3", 1),
    IndexedViewSchema(EARN_HOLDINGS_DATABASE, 1, "AEH3", 1),
    IndexedViewSchema(OBSERVED_ORDERS_DATABASE, 1, "AOO3", 1),
)

_ROOTS: dict[str, tuple[bytes, str]] = {
    SEGMENTS_DATABASE: (b"ASG3", "AccountSegmentCurrent"),
    BALANCES_DATABASE: (b"ABA3", "AccountBalanceCurrent"),
    COLLATERAL_DATABASE: (b"ACO3", "AccountCollateralCurrent"),
    POSITIONS_DATABASE: (b"APO3", "AccountPositionCurrent"),
    VALUATIONS_DATABASE: (b"AVL3", "AccountValuationCurrent"),
    EARN_HOLDINGS_DATABASE: (b"AEH3", "AccountEarnHoldingCurrent"),
    OBSERVED_ORDERS_DATABASE: (b"AOO3", "AccountObservedOrderCurrent"),
}


def account_indexed_environment_path(root: str | Path, account_id: str) -> Path:
    return (
        Path(root)
        / "views"
        / "v3"
        / "Account"
        / f"account-{account_id}"
        / "epoch-1"
        / "current.lmdb"
    )


def account_indexed_key(*parts: str) -> bytes:
    if not parts:
        raise ValueError("Account indexed key requires at least one component")
    key = bytearray((_KEY_VERSION,))
    for part in parts:
        encoded = part.encode()
        if not part or part.strip() != part or b"\0" in encoded:
            raise ValueError(
                "Account indexed key components must be non-empty and trimmed"
            )
        if len(encoded) > 0xFFFF:
            raise ValueError("Account indexed key component is too long")
        key.extend(len(encoded).to_bytes(2, "big"))
        key.extend(encoded)
    return bytes(key)


def account_indexed_key_parts(key: bytes) -> tuple[str, ...]:
    if not key or key[0] != _KEY_VERSION:
        raise ValueError("invalid Account indexed key version")
    offset = 1
    parts: list[str] = []
    while offset < len(key):
        if offset + 2 > len(key):
            raise ValueError("truncated Account indexed key length")
        length = int.from_bytes(key[offset : offset + 2], "big")
        offset += 2
        if offset + length > len(key):
            raise ValueError("truncated Account indexed key component")
        parts.append(key[offset : offset + length].decode())
        offset += length
    if not parts:
        raise ValueError("Account indexed key contains no components")
    return tuple(parts)


def decode_indexed_value(payload: bytes, database: str) -> Any:
    identifier, root_name = _ROOTS[database]
    if len(payload) < 8 or payload[4:8] != identifier:
        raise ValueError(f"invalid Account indexed value identifier for {database}")
    module = __import__(
        f"kairospy.infrastructure.protocol.generated.kairos.account.v2.{root_name}",
        fromlist=[root_name],
    )
    return getattr(module, root_name).GetRootAs(payload, 0)


class AccountIndexedViewReader:
    def __init__(
        self,
        root: str | Path,
        *,
        account_id: str,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self.account_id = account_id
        self._reader = IndexedViewReader(
            account_indexed_environment_path(root, account_id),
            map_size=ACCOUNT_MAP_SIZE,
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
            owner="Account",
            publisher_resource_id=f"account-{account_id}",
            resource_epoch=ACCOUNT_RESOURCE_EPOCH,
            schemas=ACCOUNT_INDEXED_SCHEMAS,
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def ensure_ready(self) -> IndexedViewMetadata:
        metadata = self._reader.metadata()
        if metadata.rebuild_state != "ready":
            raise RuntimeError("Account indexed current view is not ready")
        return metadata

    def values(self, database: str) -> tuple[Any, ...]:
        self.ensure_ready()
        values: list[Any] = []
        rows = self._reader.prefix(
            database,
            _ALL_VALUES_PREFIX,
            limit=MAX_INDEXED_VALUES_PER_DATABASE + 1,
        )
        _ensure_bounded(database, rows)
        for key, payload in rows:
            value = decode_indexed_value(payload, database)
            _validate(database, account_indexed_key_parts(key), value, self.account_id)
            values.append(value)
        return tuple(values)

    def snapshot(self) -> tuple[IndexedViewMetadata, dict[str, tuple[Any, ...]]]:
        databases = tuple(_ROOTS)
        snapshot = self._reader.snapshot(
            tuple(
                (
                    database,
                    _ALL_VALUES_PREFIX,
                    MAX_INDEXED_VALUES_PER_DATABASE + 1,
                )
                for database in databases
            )
        )
        if snapshot.metadata.rebuild_state != "ready":
            raise RuntimeError("Account indexed current view is not ready")
        values: dict[str, tuple[Any, ...]] = {}
        for database in databases:
            _ensure_bounded(database, snapshot.rows[database])
            decoded: list[Any] = []
            for key, payload in snapshot.rows[database]:
                value = decode_indexed_value(payload, database)
                _validate(
                    database,
                    account_indexed_key_parts(key),
                    value,
                    self.account_id,
                )
                decoded.append(value)
            values[database] = tuple(decoded)
        return snapshot.metadata, values

    def close(self) -> None:
        self._reader.close()


def _ensure_bounded(database: str, rows: tuple[tuple[bytes, bytes], ...]) -> None:
    if len(rows) > MAX_INDEXED_VALUES_PER_DATABASE:
        raise RuntimeError(
            f"Account indexed database {database} exceeds its read bound"
        )


def _validate(
    database: str, parts: tuple[str, ...], value: Any, account_id: str
) -> None:
    actual_account = _required_text(value.AccountId(), "account_id")
    if actual_account != account_id:
        raise ValueError("Account indexed account identity mismatch")
    if database == SEGMENTS_DATABASE:
        state = value.State()
        expected = (_required_text(state.SegmentKey(), "segment_key"),)
    else:
        segment = _required_text(value.SegmentKey(), "segment_key")
        entity = {
            BALANCES_DATABASE: lambda: value.Balance().AssetId(),
            COLLATERAL_DATABASE: lambda: value.Balance().AssetId(),
            POSITIONS_DATABASE: lambda: value.Position().InstrumentId(),
            VALUATIONS_DATABASE: lambda: value.SegmentKey(),
            EARN_HOLDINGS_DATABASE: lambda: value.Holding().HoldingKey(),
            OBSERVED_ORDERS_DATABASE: lambda: value.Order().SourceId(),
        }[database]
        expected = (
            (segment,)
            if database == VALUATIONS_DATABASE
            else (
                segment,
                _required_text(entity(), "entity identity"),
            )
        )
        if database == POSITIONS_DATABASE:
            expected += (
                {1: "NET", 2: "LONG", 3: "SHORT"}.get(
                    int(value.Position().PositionSide()), "UNSPECIFIED"
                ),
            )
        elif database == OBSERVED_ORDERS_DATABASE:
            expected += (_required_text(value.Order().ExecutionOrderId(), "order_id"),)
    if parts != expected:
        raise ValueError("Account indexed key/value semantic identity mismatch")


def _required_text(value: bytes | None, field: str) -> str:
    if value is None:
        raise ValueError(f"Account indexed value is missing {field}")
    return value.decode()


__all__ = [
    "ACCOUNT_INDEXED_SCHEMAS",
    "ACCOUNT_MAP_SIZE",
    "AccountIndexedViewReader",
    "BALANCES_DATABASE",
    "COLLATERAL_DATABASE",
    "EARN_HOLDINGS_DATABASE",
    "MAX_INDEXED_VALUES_PER_DATABASE",
    "OBSERVED_ORDERS_DATABASE",
    "POSITIONS_DATABASE",
    "SEGMENTS_DATABASE",
    "VALUATIONS_DATABASE",
    "account_indexed_environment_path",
    "account_indexed_key",
    "decode_indexed_value",
]
