"""Account contract facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast
from decimal import Decimal
import sys

from kairospy.application.account import (
    AccountSegmentSnapshot,
    AccountSnapshot,
    Balance,
    DataFreshness,
    EarnHolding,
    EarnHoldingState,
    EarnLiquidity,
    Position,
    PositionSide,
    SegmentCompleteness,
    SegmentSyncLifecycle,
    SegmentSyncMode,
)
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId, SegmentKey
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from .view_contract import (
    AccountViewKey,
    AccountViewKind,
    account_view_path,
    decode_view,
)
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader
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


class AccountCurrentProjection:
    """Synchronous Account application projection over one v2 current view."""

    def __init__(self, view_root: str | Path, *, account_id: AccountId) -> None:
        sys.modules.setdefault("kairos", _generated_kairos)
        self._key = AccountViewKey(
            account_runtime_id=f"account:{account_id}",
            account_id=str(account_id),
        )
        self._reader = SharedSnapshotReader(account_view_path(view_root, self._key))

    @property
    def path(self) -> Path:
        return self._reader.path

    def snapshot(self, account_id: AccountId) -> AccountSnapshot:
        snapshot = self._reader.read()
        root = cast(Any, decode_view(snapshot.payload, AccountViewKind.CURRENT))
        metadata = root.Metadata()
        if metadata is None:
            raise ValueError("Account current view metadata is missing")
        if _text(metadata.ViewKey()) != self._key.canonical_key():
            raise ValueError("Account current view key identity mismatch")
        if _text(root.AccountId()) != str(account_id):
            raise ValueError(
                f"account {account_id!s} is not present in Account projection"
            )
        generation = snapshot.generation
        if int(metadata.Generation()) != generation:
            raise ValueError("Account mmap frame and metadata generation disagree")
        if int(metadata.Completeness()) != 1:
            raise ValueError("Account mmap current view is not complete")
        event_sequence = metadata.AppliedRevision()
        if event_sequence is None:
            raise ValueError("Account mmap current view is missing applied revision")
        return AccountSnapshot(
            account_id=account_id,
            segments=tuple(
                _segment_snapshot(root.Segments(index), account_id, generation)
                for index in range(root.SegmentsLength())
            ),
            generation=generation,
            event_sequence=int(event_sequence),
        )


class AccountObservedOrdersProjection:
    """Synchronous Account projection over the observed-orders view."""

    def __init__(self, view_root: str | Path, *, account_id: AccountId) -> None:
        sys.modules.setdefault("kairos", _generated_kairos)
        self._key = AccountViewKey(
            account_runtime_id=f"account:{account_id}",
            account_id=str(account_id),
            kind=AccountViewKind.OBSERVED_ORDERS,
        )
        self._reader = SharedSnapshotReader(account_view_path(view_root, self._key))

    @property
    def path(self) -> Path:
        return self._reader.path

    def open_orders(self, account_id: AccountId) -> dict[str, object]:
        snapshot = self._reader.read()
        root = cast(Any, decode_view(snapshot.payload, AccountViewKind.OBSERVED_ORDERS))
        metadata = root.Metadata()
        if metadata is None:
            raise ValueError("Account observed-orders metadata is missing")
        if _text(metadata.ViewKey()) != self._key.canonical_key():
            raise ValueError("Account observed-orders view key identity mismatch")
        if _text(root.AccountId()) != str(account_id):
            raise ValueError(
                f"account {account_id!s} is not present in Account observed-orders projection"
            )
        return {
            "account_id": str(account_id),
            "generation": snapshot.generation,
            "event_sequence": int(metadata.AppliedRevision() or 0),
            "open_orders": [
                order
                for segment_index in range(root.SegmentsLength())
                for order in _segment_observed_orders(root.Segments(segment_index))
            ],
        }


def _segment_snapshot(
    account: Any, account_id: AccountId, generation: int
) -> AccountSegmentSnapshot:
    segment_key = SegmentKey(_text(account.SegmentKey()) or "")
    balances = tuple(
        Balance(
            account_id=account_id,
            segment_key=segment_key,
            asset=_text(value.AssetCode()) or _text(value.AssetId()) or "",
            total=_decimal64(value.Total()) or Decimal("0"),
            available=_decimal64(value.Available()) or Decimal("0"),
            reserved=_decimal64(value.Locked()) or Decimal("0"),
        )
        for value in _table_items(account, "Balances")
    )
    positions = tuple(
        Position(
            account_id=account_id,
            segment_key=segment_key,
            instrument=_instrument(_text(value.InstrumentId()) or ""),
            quantity=_decimal64(value.Quantity()) or Decimal("0"),
            position_side=_position_side(int(value.PositionSide())),
            average_price=_decimal64(value.AveragePrice()),
            market_value=_market_value(value),
            unrealized_pnl=_decimal64(value.UnrealizedPnl()),
        )
        for value in _table_items(account, "Positions")
    )
    earn_holdings = tuple(
        EarnHolding(
            account_id=account_id,
            segment_key=segment_key,
            holding_key=_text(value.HoldingKey()) or "",
            participant_position_id=_text(value.ParticipantPositionId()),
            product_id=_text(value.ProductId()) or "",
            asset=_text(value.Asset()) or "",
            principal=_decimal64(value.Principal()) or Decimal("0"),
            redeemable=_decimal64(value.Redeemable()),
            state={
                1: EarnHoldingState.ACTIVE,
                2: EarnHoldingState.REDEEMING,
                3: EarnHoldingState.REDEEMED,
            }.get(int(value.State()), EarnHoldingState.UNKNOWN),
            participant_state=_text(value.ParticipantState()),
            liquidity={
                1: EarnLiquidity.IMMEDIATE,
                2: EarnLiquidity.NOTICE,
                3: EarnLiquidity.FIXED_TERM,
            }.get(int(value.Liquidity()), EarnLiquidity.UNKNOWN),
            notice_seconds=_optional_watermark(value.NoticeSeconds()),
            matures_at_unix_nanos=_optional_watermark(value.MaturesAtUnixNanos()),
            observed_at_unix_nanos=_optional_watermark(value.ObservedAtUnixNanos()),
        )
        for value in _table_items(account, "EarnHoldings")
    )
    raw_status = account.Status()
    status = _account_status(int(raw_status))
    freshness = {
        1: DataFreshness.FRESH,
        2: DataFreshness.STALE,
        4: DataFreshness.RESYNCING,
        5: DataFreshness.UNAVAILABLE,
    }.get(int(account.Freshness()), DataFreshness.UNKNOWN)
    return AccountSegmentSnapshot(
        account_id=account_id,
        segment_key=segment_key,
        broker=_text(account.Broker()) or "",
        environment=_text(account.Environment()) or "",
        account_model=_account_model(int(account.ObservedAccountModel())),
        equity=_decimal64(
            None if account.Valuation() is None else account.Valuation().Equity()
        ),
        balances=balances,
        positions=positions,
        earn_holdings=earn_holdings,
        earn_watermark_unix_nanos=_optional_watermark(account.EarnWatermarkUnixNanos()),
        freshness=freshness,
        generation=generation,
        sync_mode={
            1: SegmentSyncMode.SNAPSHOT_THEN_STREAM,
            2: SegmentSyncMode.SNAPSHOT_ONLY,
        }.get(int(account.SyncMode()), SegmentSyncMode.UNKNOWN),
        sync_lifecycle={
            1: SegmentSyncLifecycle.CONFIGURED,
            2: SegmentSyncLifecycle.BOOTSTRAPPING,
            3: SegmentSyncLifecycle.LIVE,
            4: SegmentSyncLifecycle.SNAPSHOT_CURRENT,
            5: SegmentSyncLifecycle.DEGRADED,
            6: SegmentSyncLifecycle.RESYNCING,
            7: SegmentSyncLifecycle.UNAVAILABLE,
            8: SegmentSyncLifecycle.STOPPED,
        }.get(int(account.SyncLifecycle()), SegmentSyncLifecycle.CONFIGURED),
        completeness={
            1: SegmentCompleteness.COMPLETE,
            2: SegmentCompleteness.PARTIAL,
        }.get(int(account.Completeness()), SegmentCompleteness.UNKNOWN),
        snapshot_watermark=_optional_watermark(account.SnapshotWatermark()),
        event_watermark=_optional_watermark(account.EventWatermark()),
        channel_epoch=_optional_watermark(account.ChannelEpoch()),
        last_event_at_unix_nanos=_optional_watermark(account.LastEventAtUnixNanos()),
        last_success_at_unix_nanos=_optional_watermark(
            account.LastSuccessAtUnixNanos()
        ),
        last_error=_text(account.LastError()),
        recovery_buffer_depth=int(account.RecoveryBufferDepth()),
    )


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _segment_observed_orders(segment: Any | None) -> list[dict[str, object]]:
    if segment is None:
        raise ValueError("Account observed-orders view contains an empty segment")
    segment_key = _text(segment.SegmentKey()) or ""
    return [
        {**_observed_order(segment.Orders(index)), "segment_key": segment_key}
        for index in range(segment.OrdersLength())
    ]


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


def _table_items(value: object, name: str) -> tuple[Any, ...]:
    table = cast(Any, value)
    length = int(getattr(table, f"{name}Length")())
    result = tuple(getattr(table, name)(index) for index in range(length))
    if any(item is None for item in result):
        raise ValueError(f"Account snapshot contains an empty {name} entry")
    return cast(tuple[Any, ...], result)


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    mantissa = int(getattr(value, "Mantissa")())
    scale = int(getattr(value, "Scale")())
    return Decimal(mantissa).scaleb(-scale)


def _instrument(value: str) -> InstrumentRef:
    identifier = InstrumentId(value)
    return InstrumentRef(identifier, value.rsplit(":", 1)[-1])


def _position_side(value: int) -> PositionSide:
    return {
        0: PositionSide.NET,
        1: PositionSide.NET,
        2: PositionSide.LONG,
        3: PositionSide.SHORT,
    }.get(value, PositionSide.NET)


def _market_value(value: object) -> Decimal | None:
    quantity = _decimal64(getattr(value, "Quantity")())
    mark = _decimal64(getattr(value, "MarkPrice")())
    return None if quantity is None or mark is None else quantity * mark


def backtest_mark_to_market_request(
    event,
    *,
    segment_key: str = "spot",
    quote_asset: str = "USDT",
) -> dict[str, Any] | None:
    """Map the latest strategy-visible quote to Account mark-to-market control."""
    from kairospy.application.market import BarEvent, QuoteEvent

    if isinstance(event, BarEvent):
        observation = event.data
        mark = observation.close
        instrument_id = str(observation.instrument.id)
        event_time = observation.occurred_at_unix_nanos
    elif isinstance(event, QuoteEvent):
        observation = event.data
        prices = [
            value
            for value in (observation.bid_price, observation.ask_price)
            if value is not None
        ]
        if not prices:
            return None
        mark = sum(prices, Decimal("0")) / len(prices)
        instrument_id = str(observation.instrument.id)
        event_time = observation.occurred_at_unix_nanos
    else:
        return None
    return {
        "segment_key": segment_key,
        "instrument_id": instrument_id,
        "quote_asset": quote_asset,
        "mark_price": _decimal_wire(mark),
        "observed_at_unix_nanos": event_time,
    }


def _decimal_wire(value) -> str:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    return format(value, "f")


__all__ = [
    "AccountContractClient",
    "AccountCurrentProjection",
    "AccountObservedOrdersProjection",
    "CommandEnvelope",
    "QueryEnvelope",
    "backtest_mark_to_market_request",
]
