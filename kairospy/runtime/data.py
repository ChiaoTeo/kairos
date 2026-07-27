from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol

from kairospy.core.account import AccountContext, AccountProjection, AccountSnapshot
from kairospy.core.order import OrderEvent, OrderState
from kairospy.core.views import ViewFieldSchema, ViewSchema


RuntimeDataDomain = Literal["market", "account", "execution", "system", "clock"]


@dataclass(frozen=True, slots=True)
class RuntimeDataEnvelope:
    domain: RuntimeDataDomain | str
    kind: str
    time: datetime
    sequence: int
    payload: object
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


class RuntimeDataPipeline:
    key = "system.dataflow"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("total_count", "ingested runtime data envelope count", "runtime sequence", "runtime data pipeline"),
            ViewFieldSchema("domain_counts", "ingested envelope counts by domain", "runtime sequence", "runtime data pipeline"),
            ViewFieldSchema("latest", "latest ingested runtime data envelope summary", "event time", "runtime data pipeline"),
        ),
        mutability="runtime_writable",
        evidence="runtime data pipeline",
    )

    def __init__(self, envelopes: Iterable[RuntimeDataEnvelope] = ()) -> None:
        self._envelopes: list[RuntimeDataEnvelope] = []
        for envelope in envelopes:
            self.ingest(envelope)

    def ingest(self, envelope: RuntimeDataEnvelope) -> RuntimeDataEnvelope:
        self._envelopes.append(envelope)
        return envelope

    def records(
        self,
        *,
        domain: str | None = None,
        kind: str | None = None,
    ) -> tuple[RuntimeDataEnvelope, ...]:
        return tuple(
            envelope
            for envelope in self._envelopes
            if (domain is None or envelope.domain == domain)
            and (kind is None or envelope.kind == kind)
        )

    def latest(self, *, domain: str | None = None, kind: str | None = None) -> RuntimeDataEnvelope | None:
        records = self.records(domain=domain, kind=kind)
        return records[-1] if records else None

    def view(self) -> RuntimeDataflowView:
        counts: dict[str, int] = {}
        for envelope in self._envelopes:
            counts[str(envelope.domain)] = counts.get(str(envelope.domain), 0) + 1
        latest = self._envelopes[-1] if self._envelopes else None
        return RuntimeDataflowView(
            total_count=len(self._envelopes),
            domain_counts=tuple(sorted(counts.items())),
            latest=None if latest is None else envelope_summary(latest),
        )


@dataclass(frozen=True, slots=True)
class AccountRuntimePayload:
    context: AccountContext
    snapshot: AccountSnapshot | None = None
    projection: AccountProjection | None = None
    equity: object | None = None
    unrealized_pnl: object | None = None
    source: object | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRuntimePayload:
    order_event: OrderEvent | None = None
    state: OrderState | None = None


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
    projection: AccountProjection | None = None,
    equity: object | None = None,
    unrealized_pnl: object | None = None,
    source: object | None = None,
    metadata: Mapping[str, object] | None = None,
    stream: str | None = None,
) -> RuntimeDataEnvelope:
    return RuntimeDataEnvelope(
        "account",
        "snapshot" if snapshot is not None else "projection" if projection is not None else "event",
        time,
        sequence,
        AccountRuntimePayload(context, snapshot, projection, equity, unrealized_pnl, source),
        stream=stream or f"account.{context.environment.value}.{context.account.broker}.{context.account.account_id}",
        source="" if source is None else str(source),
        metadata=metadata or {},
    )


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
        dict(payload or {}),
        stream=stream,
        metadata=payload or {},
    )


__all__ = [
    "AccountRuntimePayload",
    "ExecutionRuntimePayload",
    "RuntimeDataDomain",
    "RuntimeDataEnvelope",
    "RuntimeDataEnvelopeSummary",
    "RuntimeDataPipeline",
    "RuntimeDataSink",
    "RuntimeDataSource",
    "RuntimeDataflowView",
    "account_data_envelope",
    "envelope_summary",
    "system_data_envelope",
]
