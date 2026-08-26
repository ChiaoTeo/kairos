"""Account contract facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from decimal import Decimal

from kairospy.primitives.account import AccountId
from .view_contract import (
    BALANCES_DATABASE,
    EARN_HOLDINGS_DATABASE,
    OBSERVED_ORDERS_DATABASE,
    POSITIONS_DATABASE,
    SEGMENTS_DATABASE,
    VALUATIONS_DATABASE,
    AccountIndexedViewReader,
)
from ..base import CommandEnvelope, QueryEnvelope
from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class AccountContractClient:
    """Low-frequency Account health and simulation-control facade.

    Live Account facts enter through Account-owned Integration capabilities.
    This client deliberately exposes no order/fill mutation API, and simulated
    fill settlement is private to Execution's durable settlement service.
    """

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._call("account_health")

    def refresh(self, request: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return self._call("account_refresh", [dict(request or {})])

    def reconcile(self, request: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return self._call("account_reconcile", [dict(request or {})])

    def mark_to_market(self, update: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._call("account_mark_to_market", [update])

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        return self._call(
            "account_advance_time",
            [{"event_time_unix_nanos": event_time_unix_nanos}],
        )

    def _call(
        self, method: str, params: list[object] | None = None
    ) -> Mapping[str, Any]:
        return self._client.call(method, params)


class AccountCurrentViewReader:
    """Synchronous Account application reader over indexed entity roots."""

    def __init__(
        self,
        view_root: str | Path,
        *,
        account_id: AccountId,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self._account_id = account_id
        self._reader = AccountIndexedViewReader(
            view_root,
            account_id=str(account_id),
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def snapshot(self, account_id: AccountId) -> dict[str, object]:
        if account_id != self._account_id:
            raise ValueError("Account reader identity does not match requested account")
        metadata, values = self._reader.snapshot()
        balances = _group_current(values[BALANCES_DATABASE], "Balance")
        positions = _group_current(values[POSITIONS_DATABASE], "Position")
        holdings = _group_current(values[EARN_HOLDINGS_DATABASE], "Holding")
        valuations = _group_current(values[VALUATIONS_DATABASE], "Valuation")
        segments = []
        for current in values[SEGMENTS_DATABASE]:
            state = current.State()
            if state is None:
                raise ValueError("Account indexed segment is missing state")
            key = _text(state.SegmentKey()) or ""
            segments.append(
                _segment_snapshot(
                    state,
                    account_id,
                    balances.get(key, ()),
                    positions.get(key, ()),
                    holdings.get(key, ()),
                    valuations.get(key, ()),
                )
            )
        generation = max(
            (int(segment["generation"]) for segment in segments), default=0
        )
        return {
            "account_id": str(account_id),
            "segments": segments,
            "generation": generation,
            "event_sequence": metadata.applied_event_sequence,
        }


class AccountObservedOrdersViewReader:
    """Synchronous Account current view over the observed-orders view."""

    def __init__(
        self,
        view_root: str | Path,
        *,
        account_id: AccountId,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self._account_id = account_id
        self._reader = AccountIndexedViewReader(
            view_root,
            account_id=str(account_id),
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def open_orders(self, account_id: AccountId) -> dict[str, object]:
        if account_id != self._account_id:
            raise ValueError("Account reader identity does not match requested account")
        metadata, values = self._reader.snapshot()
        return {
            "account_id": str(account_id),
            "generation": metadata.applied_event_sequence,
            "event_sequence": metadata.applied_event_sequence,
            "open_orders": [
                {**_observed_order(current.Order()), "segment_key": _text(current.SegmentKey()) or ""}
                for current in values[OBSERVED_ORDERS_DATABASE]
            ],
        }


def _segment_snapshot(
    account: Any,
    account_id: AccountId,
    balance_values: tuple[Any, ...],
    position_values: tuple[Any, ...],
    holding_values: tuple[Any, ...],
    valuation_values: tuple[Any, ...],
) -> dict[str, object]:
    segment_key = _text(account.SegmentKey()) or ""
    balances = tuple(
        {
            "asset": _text(value.AssetCode()) or _text(value.AssetId()) or "",
            "total": _decimal_text(_decimal64(value.Total()) or Decimal("0")),
            "available": _decimal_text(
                _decimal64(value.Available()) or Decimal("0")
            ),
            "reserved": _decimal_text(_decimal64(value.Locked()) or Decimal("0")),
        }
        for value in balance_values
    )
    positions = tuple(
        {
            "instrument_id": _text(value.InstrumentId()) or "",
            "quantity": _decimal_text(_decimal64(value.Quantity()) or Decimal("0")),
            "position_side": _position_side(int(value.PositionSide())),
            "average_price": _decimal_text(_decimal64(value.AveragePrice())),
            "market_value": _decimal_text(_market_value(value)),
            "unrealized_pnl": _decimal_text(_decimal64(value.UnrealizedPnl())),
        }
        for value in position_values
    )
    earn_holdings = tuple(
        {
            "holding_key": _text(value.HoldingKey()) or "",
            "participant_position_id": _text(value.ParticipantPositionId()),
            "product_id": _text(value.ProductId()) or "",
            "asset": _text(value.Asset()) or "",
            "principal": _decimal_text(
                _decimal64(value.Principal()) or Decimal("0")
            ),
            "redeemable": _decimal_text(_decimal64(value.Redeemable())),
            "state": {1: "active", 2: "redeeming", 3: "redeemed"}.get(
                int(value.State()), "unknown"
            ),
            "participant_state": _text(value.ParticipantState()),
            "liquidity": {1: "immediate", 2: "notice", 3: "fixed_term"}.get(
                int(value.Liquidity()), "unknown"
            ),
            "notice_seconds": _optional_watermark(value.NoticeSeconds()),
            "matures_at_unix_nanos": _optional_watermark(
                value.MaturesAtUnixNanos()
            ),
            "observed_at_unix_nanos": _optional_watermark(
                value.ObservedAtUnixNanos()
            ),
        }
        for value in holding_values
    )
    raw_status = account.Status()
    status = _account_status(int(raw_status))
    freshness = {1: "fresh", 2: "stale", 4: "resyncing", 5: "unavailable"}.get(
        int(account.Freshness()), "unknown"
    )
    return {
        "account_id": str(account_id),
        "segment_key": segment_key,
        "broker": _text(account.Broker()) or "",
        "environment": _text(account.Environment()) or "",
        "account_model": _account_model(int(account.ObservedAccountModel())),
        "equity": _decimal_text(
            _decimal64(valuation_values[0].Equity()) if valuation_values else None
        ),
        "balances": list(balances),
        "positions": list(positions),
        "earn_holdings": list(earn_holdings),
        "earn_watermark_unix_nanos": _optional_watermark(
            account.EarnWatermarkUnixNanos()
        ),
        "freshness": freshness,
        "generation": int(account.StateGeneration()),
        "sync_mode": {1: "snapshot_then_stream", 2: "snapshot_only"}.get(
            int(account.SyncMode()), "unknown"
        ),
        "sync_lifecycle": {
            1: "configured",
            2: "bootstrapping",
            3: "live",
            4: "snapshot_current",
            5: "degraded",
            6: "resyncing",
            7: "unavailable",
            8: "stopped",
        }.get(int(account.SyncLifecycle()), "configured"),
        "completeness": {1: "complete", 2: "partial"}.get(
            int(account.Completeness()), "unknown"
        ),
        "snapshot_watermark": _optional_watermark(account.SnapshotWatermark()),
        "event_watermark": _optional_watermark(account.EventWatermark()),
        "channel_epoch": _optional_watermark(account.ChannelEpoch()),
        "last_event_at_unix_nanos": _optional_watermark(
            account.LastEventAtUnixNanos()
        ),
        "last_success_at_unix_nanos": _optional_watermark(
            account.LastSuccessAtUnixNanos()
        ),
        "last_error": _text(account.LastError()),
        "recovery_buffer_depth": int(account.RecoveryBufferDepth()),
    }


def _group_current(
    values: tuple[Any, ...], accessor: str
) -> dict[str, tuple[Any, ...]]:
    grouped: dict[str, list[Any]] = {}
    for current in values:
        segment_key = _text(current.SegmentKey()) or ""
        value = getattr(current, accessor)()
        if value is None:
            raise ValueError(f"Account indexed {accessor} value is missing")
        grouped.setdefault(segment_key, []).append(value)
    return {key: tuple(items) for key, items in grouped.items()}


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _observed_order(order: Any | None) -> dict[str, object]:
    if order is None:
        raise ValueError("Account observed-orders view contains an empty order")
    return {
        "observation_id": _text(order.ObservationId()),
        "source_id": _text(order.SourceId()),
        "execution_order_id": _text(order.ExecutionOrderId()),
        "remote_order_id": _text(order.RemoteOrderId()),
        "instrument_id": _text(order.InstrumentId()),
        "market_id": _text(order.MarketId()),
        "side": _observed_order_side(int(order.Side())),
        "quantity": _decimal64(order.Quantity()),
        "filled_quantity": _decimal64(order.FilledQuantity()),
        "status": _observed_order_status(int(order.Status())),
        "observed_at_unix_nanos": int(order.ObservedAtUnixNanos()),
    }


def _observed_order_side(value: int) -> str:
    return {1: "buy", 2: "sell"}.get(value, f"unknown:{value}")


def _observed_order_status(value: int) -> str:
    return {
        1: "open",
        2: "partially_filled",
        3: "pending_cancel",
        4: "closed",
        5: "unknown",
    }.get(value, f"unknown:{value}")


def _optional_watermark(value: int) -> int | None:
    return None if int(value) == 0 else int(value)


def _account_status(value: int) -> str:
    return {
        1: "active",
        2: "restricted",
        6: "reconciling",
        7: "type_mismatch",
        8: "unavailable",
    }.get(value, "unknown")


def _account_model(value: int) -> str | None:
    return {1: "cash", 2: "margin", 3: "portfolio_margin"}.get(value)


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    mantissa = int(getattr(value, "Mantissa")())
    scale = int(getattr(value, "Scale")())
    return Decimal(mantissa).scaleb(-scale)


def _position_side(value: int) -> str:
    return {0: "net", 1: "net", 2: "long", 3: "short"}.get(value, "net")


def _market_value(value: object) -> Decimal | None:
    quantity = _decimal64(getattr(value, "Quantity")())
    mark = _decimal64(getattr(value, "MarkPrice")())
    return None if quantity is None or mark is None else quantity * mark


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


__all__ = [
    "AccountContractClient",
    "AccountCurrentViewReader",
    "AccountObservedOrdersViewReader",
    "CommandEnvelope",
    "QueryEnvelope",
]
