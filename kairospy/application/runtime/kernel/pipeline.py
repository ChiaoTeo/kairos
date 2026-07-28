from __future__ import annotations

from collections.abc import Iterable

from kairospy.core.views import ViewFieldSchema, ViewSchema

from ..model.data import RuntimeDataEnvelope, RuntimeDataflowView, envelope_summary


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


__all__ = ["RuntimeDataPipeline"]
