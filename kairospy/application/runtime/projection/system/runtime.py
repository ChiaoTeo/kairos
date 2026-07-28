from __future__ import annotations

from datetime import datetime

from kairospy.application.context import DataContext
from kairospy.application.context.control import ControlJournal
from kairospy.core.views import ViewStore

from ...kernel.pipeline import RuntimeDataPipeline
from ...model import RuntimeDataEnvelope
from .views import ControlJournalView, ControlRequestSummary, StrategyRunView


class RuntimeSystemProjection:
    def __init__(
        self,
        *,
        strategy_id: str,
        data: DataContext,
        data_pipeline: RuntimeDataPipeline,
        controls: ControlJournal,
    ) -> None:
        self.strategy_id = strategy_id
        self.data = data
        self.data_pipeline = data_pipeline
        self.controls = controls

    def publish(
        self,
        views: ViewStore,
        *,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        last_runtime_event: RuntimeDataEnvelope | None,
        status: str,
    ) -> datetime | None:
        as_of = _event_time(last_runtime_event) or (last_event.time if last_event is not None else None)
        views.put_runtime(
            "system.strategy",
            StrategyRunView(
                strategy_id=self.strategy_id,
                event_count=event_count,
                runtime_event_count=runtime_event_count,
                last_event_time=last_event.time if last_event is not None else None,
                last_stream=last_event.stream if last_event is not None else None,
                last_runtime_event_time=as_of,
                last_runtime_stream=_event_stream(last_runtime_event),
                status=status,
            ),
            as_of=as_of,
            available_time=as_of,
        )
        views.put_runtime("system.data", self.data.snapshot())
        views.put_runtime("system.dataflow", self.data_pipeline.view(), as_of=as_of, available_time=as_of)
        views.put_runtime("system.control", self._control_view())
        return as_of

    def _control_view(self) -> ControlJournalView:
        requests = self.controls.list(strategy_id=self.strategy_id)
        summaries = tuple(
            ControlRequestSummary(
                request_id=item.request_id,
                strategy_id=item.strategy_id,
                kind=item.kind.value,
                requested_at=item.requested_at,
                payload=tuple(sorted(item.payload.items())),
                reason=item.reason,
            )
            for item in requests
        )
        return ControlJournalView(total_count=len(summaries), requests=summaries)


def _event_time(event: RuntimeDataEnvelope | None) -> datetime | None:
    return None if event is None else event.time


def _event_stream(event: RuntimeDataEnvelope | None) -> str | None:
    if event is None:
        return None
    return event.stream or None


__all__ = ["RuntimeSystemProjection"]
