"""Application-level assembly for CLI command services."""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.composition.application.resources import command_resources, market_command_resources
from kairospy.application.usecases.account.application.commands import AccountCommandApplication
from kairospy.application.usecases.execution.application.commands import OrderCommandApplication
from kairospy.application.usecases.reference.application.commands import ReferenceCommandApplication
from kairospy.application.usecases.reference.application.component import ReferenceApplication
from kairospy.application.usecases.market.application.commands import (
    MarketBacktestPrefetchCommandService,
    MarketDataQueryService,
    MarketDatasetCommandService,
    MarketHistoricalCommandService,
    MarketReplayCommandService,
    MarketSourceQueryService,
    MarketStreamCommandService,
)


@dataclass(frozen=True, slots=True)
class MarketCommandServices:
    source: MarketSourceQueryService
    historical: MarketHistoricalCommandService
    query: MarketDataQueryService
    datasets: MarketDatasetCommandService
    replay: MarketReplayCommandService
    stream: MarketStreamCommandService
    prefetch: MarketBacktestPrefetchCommandService


def build_account_command() -> AccountCommandApplication:
    return AccountCommandApplication(command_resources())  # type: ignore[arg-type]


def build_order_command() -> OrderCommandApplication:
    return OrderCommandApplication(command_resources())  # type: ignore[arg-type]


def build_reference_command() -> ReferenceCommandApplication:
    return ReferenceCommandApplication(command_resources())  # type: ignore[arg-type]


def build_reference_application(root: str | None = None) -> ReferenceApplication:
    resources = command_resources()
    return ReferenceApplication(resources.reference_store(root))  # type: ignore[union-attr]


def build_market_commands() -> MarketCommandServices:
    resources = market_command_resources()
    query = MarketDataQueryService(resources)
    return MarketCommandServices(
        source=MarketSourceQueryService(resources),
        historical=MarketHistoricalCommandService(resources),
        query=query,
        datasets=MarketDatasetCommandService(resources),
        replay=MarketReplayCommandService(resources, query),
        stream=MarketStreamCommandService(resources),
        prefetch=MarketBacktestPrefetchCommandService(resources),
    )


__all__ = [
    "MarketCommandServices",
    "build_account_command",
    "build_market_commands",
    "build_order_command",
    "build_reference_command",
    "build_reference_application",
]
