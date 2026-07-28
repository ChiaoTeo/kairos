from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol, TypeAlias

from kairospy.core.account import AccountContext, AccountState, AccountSnapshot, AccountSource
from kairospy.core.market import MarketEvent
from kairospy.core.order import OrderEvent, OrderState


RuntimeDataDomain = Literal["market", "account", "execution", "system", "clock"]
ExternalRuntimePayload: TypeAlias = Mapping[str, object]
AccountRuntimeSource: TypeAlias = AccountSource | str
AccountRuntimeAmount: TypeAlias = Decimal | int | float | str


@dataclass(frozen=True, slots=True)
class AccountRuntimePayload:
    context: AccountContext
    snapshot: AccountSnapshot | None = None
    account_state: AccountState | None = None
    pending_orders: tuple[OrderState, ...] = ()
    equity: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    source: AccountRuntimeSource | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pending_orders", tuple(self.pending_orders))
        object.__setattr__(self, "equity", _optional_decimal(self.equity, "account equity"))
        object.__setattr__(self, "unrealized_pnl", _optional_decimal(self.unrealized_pnl, "account unrealized_pnl"))
        if isinstance(self.source, str) and not self.source.strip():
            raise ValueError("account runtime source cannot be blank")


@dataclass(frozen=True, slots=True)
class ExecutionRuntimePayload:
    order_event: OrderEvent | None = None
    state: OrderState | None = None


@dataclass(frozen=True, slots=True)
class SystemRuntimePayload(Mapping[str, object]):
    fields: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    def __getitem__(self, key: str) -> object:
        return self.fields[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)


RuntimePayload: TypeAlias = (
    MarketEvent
    | AccountRuntimePayload
    | ExecutionRuntimePayload
    | SystemRuntimePayload
    | ExternalRuntimePayload
)


@dataclass(frozen=True, slots=True)
class RuntimeDataEnvelope:
    domain: RuntimeDataDomain | str
    kind: str
    time: datetime
    sequence: int
    payload: RuntimePayload
    stream: str = ""
    source: str = ""
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.domain.strip() or not self.kind.strip():
            raise ValueError("runtime data envelope domain and kind are required")
        if self.time.tzinfo is None:
            raise ValueError("runtime data envelope time must be timezone-aware")
        if self.sequence < 1:
            raise ValueError("runtime data envelope sequence must be positive")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RuntimeDataEnvelopeSummary:
    domain: str
    kind: str
    time: datetime
    sequence: int
    stream: str = ""
    source: str = ""
    payload_type: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeDataflowView:
    total_count: int = 0
    domain_counts: tuple[tuple[str, int], ...] = ()
    latest: RuntimeDataEnvelopeSummary | None = None


class RuntimeDataSource(Protocol):
    def records(self) -> Iterable[RuntimeDataEnvelope]:
        ...


class RuntimeDataSink(Protocol):
    def ingest(self, envelope: RuntimeDataEnvelope) -> RuntimeDataEnvelope:
        ...


def envelope_summary(envelope: RuntimeDataEnvelope) -> RuntimeDataEnvelopeSummary:
    return RuntimeDataEnvelopeSummary(
        domain=str(envelope.domain),
        kind=envelope.kind,
        time=envelope.time,
        sequence=envelope.sequence,
        stream=envelope.stream,
        source=envelope.source,
        payload_type=type(envelope.payload).__name__,
    )


def account_data_envelope(
    context: AccountContext,
    *,
    sequence: int,
    time: datetime,
    snapshot: AccountSnapshot | None = None,
    account_state: AccountState | None = None,
    pending_orders: Iterable[OrderState] = (),
    equity: AccountRuntimeAmount | None = None,
    unrealized_pnl: AccountRuntimeAmount | None = None,
    source: AccountRuntimeSource | None = None,
    metadata: Mapping[str, object] | None = None,
    stream: str | None = None,
) -> RuntimeDataEnvelope:
    return RuntimeDataEnvelope(
        "account",
        "snapshot" if snapshot is not None else "state" if account_state is not None else "event",
        time,
        sequence,
        AccountRuntimePayload(context, snapshot, account_state, tuple(pending_orders), equity, unrealized_pnl, source),
        stream=stream or f"account.{context.environment.value}.{context.account.broker}.{context.account.account_id}",
        source="" if source is None else str(source),
        metadata=metadata or {},
    )


def _optional_decimal(value: object | None, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{label} must be decimal-compatible") from error


def system_data_envelope(
    name: str,
    *,
    sequence: int,
    time: datetime,
    payload: Mapping[str, object] | None = None,
    stream: str = "system",
) -> RuntimeDataEnvelope:
    return RuntimeDataEnvelope(
        "system",
        name,
        time,
        sequence,
        SystemRuntimePayload(payload or {}),
        stream=stream,
        metadata=payload or {},
    )


__all__ = [
    "AccountRuntimePayload",
    "AccountRuntimeAmount",
    "AccountRuntimeSource",
    "ExecutionRuntimePayload",
    "ExternalRuntimePayload",
    "RuntimeDataDomain",
    "RuntimeDataEnvelope",
    "RuntimeDataEnvelopeSummary",
    "RuntimePayload",
    "RuntimeDataSink",
    "RuntimeDataSource",
    "RuntimeDataflowView",
    "SystemRuntimePayload",
    "account_data_envelope",
    "envelope_summary",
    "system_data_envelope",
]
