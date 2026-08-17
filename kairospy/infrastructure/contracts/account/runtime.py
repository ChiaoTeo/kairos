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
    Position,
)
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId, SegmentKey
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from .view_contract import (
    AccountViewKey,
    AccountViewKind,
    decode_view,
)
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader
from ..base import CommandEnvelope, QueryEnvelope
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class AccountContractClient:
    """Low-frequency Account health and simulation-control facade.

    Live Account facts enter through Account-owned Integration capabilities.
    This client deliberately exposes no order/fill mutation API, and simulated
    fill settlement is private to Execution's durable settlement service.
    """

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._get("/v1/health")

    def mark_to_market(self, update: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/mark-to-market", update)

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        return self._post(
            "/v1/time/advance",
            {"event_time_unix_nanos": event_time_unix_nanos},
        )

    def _get(self, path: str, **params: object) -> Mapping[str, Any]:
        query = "&".join(
            f"{key}={value}" for key, value in params.items() if value is not None
        )
        status, value = self._client.request(
            "GET", f"{path}?{query}" if query else path
        )
        return _response(status, value)

    def _post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        status, value = self._client.request("POST", path, body)
        return _response(status, value)


def _response(status: int, value: Mapping[str, Any]) -> Mapping[str, Any]:
    if status >= 400:
        raise RuntimeError(
            str(value.get("error", f"Account request failed: HTTP {status}"))
        )
    return value


class AccountProjection:
    """Synchronous Account application projection over one v2 current view."""

    def __init__(self, path: str | Path, *, account_id: AccountId) -> None:
        sys.modules.setdefault("kairos", _generated_kairos)
        self._key = AccountViewKey(
            account_runtime_id=f"account:{account_id}",
            account_id=str(account_id),
        )
        self._reader = SharedSnapshotReader(path)

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
            raise ValueError(f"account {account_id!s} is not present in Account projection")
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
            segments=tuple(_segment_snapshot(root.Segments(index), account_id, generation) for index in range(root.SegmentsLength())),
            generation=generation,
            event_sequence=int(event_sequence),
        )


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
            average_price=_decimal64(value.AveragePrice()),
            market_value=_market_value(value),
            unrealized_pnl=_decimal64(value.UnrealizedPnl()),
        )
        for value in _table_items(account, "Positions")
    )
    raw_status = account.Status()
    status = _account_status(int(raw_status))
    freshness = (
        DataFreshness.STALE
        if int(account.Freshness()) == 2
        else DataFreshness.RESYNCING
        if status == "reconciling"
        else DataFreshness.UNAVAILABLE
        if status in {"unavailable", "suspended"}
        else DataFreshness.FRESH
        if status in {"active", "ready"}
        else DataFreshness.UNKNOWN
    )
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
        freshness=freshness,
        generation=generation,
    )


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _account_status(value: int) -> str:
    return {1: "active", 2: "restricted", 6: "reconciling", 7: "type_mismatch", 8: "unavailable"}.get(value, "unknown")


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


def _market_value(value: object) -> Decimal | None:
    quantity = _decimal64(getattr(value, "Quantity")())
    mark = _decimal64(getattr(value, "MarkPrice")())
    return None if quantity is None or mark is None else quantity * mark


def backtest_mark_to_market(
    path: str | Path,
    event,
    *,
    segment_key: str = "spot",
    quote_asset: str = "USDT",
) -> Mapping[str, Any] | None:
    """Apply the latest strategy-visible quote to Account during replay."""
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
    client = AccountContractClient(path)
    result = client.mark_to_market(
        {
            "segment_key": segment_key,
            "instrument_id": instrument_id,
            "quote_asset": quote_asset,
            "mark_price": _decimal_wire(mark),
            "observed_at_unix_nanos": event_time,
        }
    )
    return {
        "result": result,
        "segment_key": segment_key,
    }


def _decimal_wire(value) -> str:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    return format(value, "f")


__all__ = [
    "AccountContractClient",
    "AccountProjection",
    "CommandEnvelope",
    "QueryEnvelope",
    "backtest_mark_to_market",
]
