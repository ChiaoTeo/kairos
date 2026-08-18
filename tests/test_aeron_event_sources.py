from __future__ import annotations

import pytest

from kairospy.infrastructure.transport import native_event
from kairospy.infrastructure.transport.account import AeronAccountEventSource
from kairospy.infrastructure.transport.execution import AeronExecutionEventSource
from kairospy.infrastructure.transport.generated_spec import (
    ACCOUNT_EVENTS,
    EXECUTION_EVENTS,
    MARKET_EVENTS,
    RISK_EVENTS,
)
from kairospy.infrastructure.transport.market import AeronMarketEventSource
from kairospy.infrastructure.transport.risk import AeronRiskEventSource


@pytest.mark.parametrize(
    ("source_type", "stream_id"),
    (
        (AeronMarketEventSource, MARKET_EVENTS),
        (AeronAccountEventSource, ACCOUNT_EVENTS),
        (AeronExecutionEventSource, EXECUTION_EVENTS),
        (AeronRiskEventSource, RISK_EVENTS),
    ),
)
def test_aeron_sources_use_the_generated_native_stream_spec(
    source_type, stream_id: int
) -> None:
    source = source_type(aeron_dir="/workspace/run/aeron/media")

    assert source._aeron_dir == "/workspace/run/aeron/media"
    assert source._spec.stream_id == stream_id


@pytest.mark.parametrize(
    "source_type",
    (
        AeronMarketEventSource,
        AeronAccountEventSource,
        AeronRiskEventSource,
    ),
)
def test_live_aeron_source_readiness_opens_and_closes_native_subscription(
    monkeypatch: pytest.MonkeyPatch, source_type
) -> None:
    opened: list[tuple[object, str | None, int]] = []

    class Subscription:
        def __init__(self, spec, *, aeron_dir, queue_capacity) -> None:
            opened.append((spec, aeron_dir, queue_capacity))
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(native_event.native, "AeronSubscription", Subscription)
    source = source_type(aeron_dir="/workspace/run/aeron/media")

    source.check_ready()

    assert len(opened) == 1
    assert opened[0][1:] == ("/workspace/run/aeron/media", 1024)
    assert source._subscription is None


def test_execution_readiness_retains_subscription_for_snapshot_live_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = []

    class Subscription:
        def __init__(self, spec, *, aeron_dir, queue_capacity) -> None:
            opened.append(self)

        def close(self) -> None:
            raise AssertionError("Execution readiness must retain the subscription")

    monkeypatch.setattr(native_event.native, "AeronSubscription", Subscription)
    source = AeronExecutionEventSource(aeron_dir="/workspace/run/aeron/media")

    source.check_ready()

    assert len(opened) == 1
    assert source._subscription is opened[0]
