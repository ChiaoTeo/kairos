from __future__ import annotations

import asyncio

import pytest

from kairospy.infrastructure.transport.account import AeronAccountEventSource
from kairospy.infrastructure.transport.execution import AeronExecutionEventSource
from kairospy.infrastructure.transport.market import AeronMarketEventSource
from kairospy.infrastructure.transport.risk import AeronRiskEventSource


@pytest.mark.parametrize(
    ("source_type", "message"),
    (
        (AeronMarketEventSource, "Market Aeron bridge ended unexpectedly"),
        (AeronAccountEventSource, "Account Aeron bridge ended unexpectedly"),
        (AeronExecutionEventSource, "Execution Aeron bridge ended unexpectedly"),
        (AeronRiskEventSource, "Risk Aeron bridge ended unexpectedly"),
    ),
)
def test_live_aeron_source_never_treats_bridge_exit_as_end_of_stream(
    source_type, message: str
) -> None:
    source = source_type(binary="/usr/bin/true")

    async def collect() -> None:
        async for _ in source.events():
            pass

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(collect())


@pytest.mark.parametrize(
    "source_type",
    (
        AeronMarketEventSource,
        AeronAccountEventSource,
        AeronExecutionEventSource,
        AeronRiskEventSource,
    ),
)
def test_aeron_sources_bind_the_workspace_media_driver(source_type) -> None:
    source = source_type(binary="bridge", aeron_dir="/workspace/run/aeron/media")

    assert source._command()[-2:] == ["--aeron-dir", "/workspace/run/aeron/media"]


@pytest.mark.parametrize(
    "source_type",
    (
        AeronMarketEventSource,
        AeronAccountEventSource,
        AeronExecutionEventSource,
        AeronRiskEventSource,
    ),
)
def test_live_aeron_source_readiness_executes_bridge_probe(source_type) -> None:
    source_type(binary="/usr/bin/true").check_ready()
    with pytest.raises(RuntimeError, match="readiness failed"):
        source_type(binary="/usr/bin/false").check_ready()
