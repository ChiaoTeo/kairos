from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

from kairospy.application.account.events import (
    AccountChangeRecord,
    AccountEventRecord,
    AccountFactProvenanceRecord,
)
from kairospy.infrastructure.transport.native_event import NativeEventSource
from kairospy.infrastructure.transport.generated_spec import (
    ACCOUNT_EVENTS,
    DEFAULT_CHANNEL,
)
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


class AeronAccountEventSource(NativeEventSource[AccountEventRecord]):
    """Account-owned subprocess adapter over the native Aeron subscription."""

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = ACCOUNT_EVENTS,
    ) -> None:
        super().__init__(
            decoder=decode_account_event,
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )


def decode_account_event(payload: bytes) -> AccountEventRecord:
    if len(payload) < 8:
        raise ValueError("Account event frame is too short")
    identifier = payload[4:8]
    roots = {
        b"ABU2": ("BalanceUpserted", "balance_changed"),
        b"ABR2": ("BalanceRemoved", "balance_removed"),
        b"APU2": ("PositionUpserted", "position_changed"),
        b"APR2": ("PositionRemoved", "position_removed"),
        b"AVC2": ("ValuationChanged", "equity_changed"),
        b"ASC2": ("AccountStatusChanged", "status_changed"),
        b"AOU2": ("ObservedOrderUpserted", "observed_order_changed"),
        b"AOR2": ("ObservedOrderRemoved", "observed_order_removed"),
    }
    try:
        root_name, kind = roots[identifier]
    except KeyError as error:
        raise ValueError(f"unsupported Account event identifier: {identifier!r}") from error
    module = __import__(
        f"kairospy.infrastructure.transport.generated.kairos.account.v2.{root_name}",
        fromlist=[root_name],
    )
    root = cast(Any, getattr(module, root_name).GetRootAs(payload, 0))
    metadata = cast(Any, root.Metadata())
    if metadata is None:
        raise ValueError("Account event metadata is missing")
    change = _decode_v2_change(root, kind)
    raw_provenance = cast(Any, root.Provenance())
    return AccountEventRecord(
        stream_id=_required_text(metadata.StreamId(), "stream_id"),
        sequence=int(metadata.Sequence()),
        producer=_required_text(metadata.ProducerId(), "producer_id"),
        account_id=_required_text(root.AccountId(), "account_id"),
        changes=(change,),
        occurred_at_unix_nanos=int(metadata.OccurredAtUnixNanos()),
        launch_id=_optional_text(metadata.LaunchId()),
        instance_id=_optional_text(metadata.InstanceId()),
        provenance=(
            None
            if raw_provenance is None
            else AccountFactProvenanceRecord(
                source_id=_required_text(raw_provenance.SourceId(), "provenance.source_id"),
                provider_event_id=_optional_text(raw_provenance.ProviderEventId()),
                provider_sequence=_optional_int(raw_provenance.ProviderSequence()),
                provider_occurred_at_unix_nanos=_optional_int(
                    raw_provenance.ProviderOccurredAtUnixNanos()
                ),
                provider_received_at_unix_nanos=_optional_int(
                    raw_provenance.ProviderReceivedAtUnixNanos()
                ),
            )
        ),
    )


def _decode_v2_change(root: Any, kind: str) -> AccountChangeRecord:
    segment_key = _required_text(root.SegmentKey(), "change.segment_key")
    if kind == "balance_changed":
        value = cast(Any, root.Balance())
        if value is None:
            raise ValueError("Account balance change payload is missing")
        payload: object = {
            "asset_id": _required_text(value.AssetId(), "balance.asset_id"),
            "asset": _required_text(value.AssetCode(), "balance.asset_code"),
            "total": _decimal(value.Total()),
            "available": _decimal(value.Available()),
            "reserved": _decimal(value.Locked()),
        }
    elif kind == "balance_removed":
        payload = {"asset_id": _required_text(root.AssetId(), "balance.asset_id")}
    elif kind == "position_changed":
        value = cast(Any, root.Position())
        if value is None:
            raise ValueError("Account position change payload is missing")
        payload = {
            "instrument_id": _required_text(
                value.InstrumentId(), "position.instrument_id"
            ),
            "quantity": _decimal(value.Quantity()),
            "average_price": _decimal(value.AveragePrice()),
            "market_value": _market_value(value),
            "unrealized_pnl": _decimal(value.UnrealizedPnl()),
        }
    elif kind == "position_removed":
        payload = {
            "instrument_id": _required_text(root.InstrumentId(), "position.instrument_id"),
            "market_id": _optional_text(root.MarketId()),
        }
    elif kind == "observed_order_changed":
        value = cast(Any, root.Order())
        if value is None:
            raise ValueError("Account observed-order change payload is missing")
        payload = {
            "order_id": _optional_text(value.ExecutionOrderId()),
            "remote_order_id": _optional_text(value.RemoteOrderId()),
            "instrument_id": _required_text(value.InstrumentId(), "order.instrument_id"),
            "market_id": _required_text(value.MarketId(), "order.market_id"),
            "quantity": _decimal(value.Quantity()),
            "filled_quantity": _decimal(value.FilledQuantity()),
            "status": _order_status_name(int(value.Status())),
        }
    elif kind == "observed_order_removed":
        payload = {
            "order_id": _optional_text(root.ExecutionOrderId()) or _required_text(root.ObservationId(), "order.observation_id"),
            "remote_order_id": _optional_text(root.RemoteOrderId()),
        }
    elif kind == "equity_changed":
        value = cast(Any, root.Valuation())
        if value is None:
            raise ValueError("Account valuation change payload is missing")
        payload = {"equity": _decimal(value.Equity())}
    elif kind == "status_changed":
        payload = {
            "status": _status_name(int(root.Status())),
            "stale": int(root.Freshness()) == 2,
            "trading_enabled": int(root.Status()) == 1,
        }
    else:
        raise ValueError(f"unsupported Account event kind: {kind}")
    return AccountChangeRecord(kind, segment_key, payload)


def _status_name(value: int) -> str:
    return {
        1: "active",
        2: "restricted",
        6: "reconciling",
        7: "type_mismatch",
        8: "unavailable",
    }.get(value, "unknown")


def _order_status_name(value: int) -> str:
    return {1: "open", 2: "partially_filled", 3: "pending_cancel", 4: "closed", 5: "unknown"}.get(value, "unknown")


def _decimal(value: object | None) -> str | None:
    if value is None:
        return None
    mantissa = int(getattr(value, "Mantissa")())
    scale = int(getattr(value, "Scale")())
    sign = "-" if mantissa < 0 else ""
    digits = str(abs(mantissa)).rjust(scale + 1, "0")
    return (
        f"{sign}{digits}"
        if scale == 0
        else f"{sign}{digits[:-scale]}.{digits[-scale:]}"
    )


def _optional_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None


def _optional_int(value: int | None) -> int | None:
    return None if value is None else int(value)


def _market_value(value: Any) -> str | None:
    quantity = _decimal(value.Quantity())
    mark = _decimal(value.MarkPrice())
    if quantity is None or mark is None:
        return None
    from decimal import Decimal

    return str(Decimal(quantity) * Decimal(mark))


def _required_text(value: bytes | None, name: str) -> str:
    result = "" if value is None else value.decode()
    if not result.strip():
        raise ValueError(f"Account event {name} is required")
    return result


__all__ = ["AeronAccountEventSource", "decode_account_event"]
