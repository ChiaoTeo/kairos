"""Public data capability for the market usecase."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kairospy.application.usecases.market.domain.datasets import MarketPartition, parse_market_dataset_id
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import (
    DataSubscription,
    DataSubscriptionGroup,
    MarketDataSubscriptionGroupSpec,
    MarketDataSubscriptionSpec,
)
from kairospy.application.usecases.market.services.operations import MarketDataOperationsService
from kairospy.application.usecases.market.services.resolver import MarketDataResolver, ResolvedMarketData
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketTime, MarketWarmupStatus
from kairospy.application.usecases.market.application.requests import MarketOptions
from kairospy.application.usecases.market.protocol import MarketDataStore, MarketHistoricalClient
from kairospy.domain.market import MarketEvent, RateObservation
from kairospy.application.usecases.market.application.integration import MarketDataConnectionRequest, MarketIntegrationRuntime
from kairospy.domain.market import Bar
from kairospy.domain.reference import MarketRef


class MarketDataApplicationService:
    """Narrow market capability used by composed market business tasks."""

    def __init__(
        self,
        store: MarketDataStore | None = None,
        *,
        resolver: MarketDataResolver | None = None,
        integration_runtime: MarketIntegrationRuntime | None = None,
        empty_cache_seconds: float = 24 * 60 * 60,
        failure_cooldown_seconds: float = 5 * 60,
    ) -> None:
        if empty_cache_seconds < 0 or failure_cooldown_seconds < 0:
            raise ValueError("market data cache cooldowns cannot be negative")
        self._operations = None if store is None else MarketDataOperationsService(store, resolver=resolver)
        self._resolver = resolver or MarketDataResolver()
        self._integration_runtime = integration_runtime
        self._empty_cache_seconds = empty_cache_seconds
        self._failure_cooldown_seconds = failure_cooldown_seconds

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._require_operations().resolve(spec)

    @property
    def store(self) -> MarketDataStore:
        return self._require_operations().store

    @property
    def has_store(self) -> bool:
        return self._operations is not None

    @property
    def resolver(self) -> MarketDataResolver:
        return self._resolver

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[MarketDataRow]:
        return self._require_operations().read(spec, columns=columns)

    def download(self, spec: MarketDataSpec, client: MarketHistoricalClient | None = None, *, mode: str = "append", options: MarketOptions | None = None) -> Path:
        if client is None:
            client = self._require_integration().create_data(MarketDataConnectionRequest(spec, params=options or {}))
        return self._require_operations().download(spec, client, mode=mode, params=options)

    def persist_historical(self, spec: MarketDataSpec, observations: Iterable[Bar | RateObservation], *, mode: str = "append") -> Path:
        return self._require_operations().persist_historical(spec, observations, mode=mode)

    def ensure(
        self,
        spec: MarketDataSpec,
        client: MarketHistoricalClient | None = None,
        *,
        mode: str = "append",
        options: MarketOptions | None = None,
    ) -> ResolvedMarketData:
        if client is None:
            client = self._require_integration().create_data(MarketDataConnectionRequest(spec, params=options or {}))
        return self._require_operations().ensure(spec, client, mode=mode, params=options)

    def ensure_bars(self, spec: MarketDataSpec, client: MarketHistoricalClient) -> tuple[Bar, ...]:
        """Load persisted bars, fetching and writing them only when absent."""
        operations = self._require_operations()
        storage_spec = _storage_spec(spec)
        rows = operations.read(storage_spec)
        if not rows:
            market = MarketRef.ephemeral(
                venue=str(spec.venue or ""),
                market=str(spec.market or ""),
                source_symbol=spec.symbol,
            )
            status_key = _historical_status_key(operations, storage_spec)
            cached_status = _read_metadata(operations.store, status_key)
            if _status_is_cooling_down(cached_status):
                return ()
            try:
                fetched = tuple(client.bars(
                    market.source_symbol,
                    timeframe=storage_spec.timeframe or "1m",
                    since=storage_spec.start,
                    until=storage_spec.end,
                    limit=storage_spec.limit or 1000,
                    adapter_options={"market": str(market.market)},
                ))
            except Exception as error:
                _write_metadata(operations.store, status_key, _historical_status(
                    "failed", error_type=type(error).__name__, cooldown=self._failure_cooldown_seconds,
                ))
                raise
            if not fetched:
                _write_metadata(operations.store, status_key, _historical_status(
                    "empty", cooldown=self._empty_cache_seconds,
                ))
                return ()
            operations.persist_historical(storage_spec, fetched, mode="append")
            _write_metadata(operations.store, status_key, _historical_status("ready", cooldown=0))
            return tuple(fetched)
        market = MarketRef.ephemeral(
            venue=str(spec.venue or ""),
            market=str(spec.market or ""),
            source_symbol=spec.symbol,
        )
        return tuple(_bar_from_row(row, spec=spec, market=market) for row in rows)

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[MarketEvent],
        *,
        limit: int | None = None,
    ) -> int:
        return await self._require_operations().persist(spec, events, limit=limit)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._require_operations().partition_for(resolved)

    def partition_for_spec(self, spec: MarketDataSpec) -> MarketPartition:
        return self._require_operations().partition_for_spec(spec)

    def _require_operations(self) -> MarketDataOperationsService:
        if self._operations is None:
            raise RuntimeError("market data application requires a dataset store for data operations")
        return self._operations

    def _require_integration(self) -> MarketIntegrationRuntime:
        if self._integration_runtime is None:
            raise RuntimeError("market data operation requires a MarketIntegrationRuntime")
        return self._integration_runtime


def _bar_from_row(row: MarketDataRow, *, spec: MarketDataSpec, market: MarketRef) -> Bar:
    observed = row.get("time")
    if isinstance(observed, str):
        text = observed[:-1] + "+00:00" if observed.endswith("Z") else observed
        observed = datetime.fromisoformat(text)
    if not isinstance(observed, datetime):
        raise TypeError("persisted market bar time must be a datetime")
    if observed.tzinfo is None or observed.utcoffset() is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return Bar(
        instrument_id=str(row.get("instrument_id") or market.instrument_id),
        market_id=str(row.get("market_id") or market.market_id),
        market_key=str(row.get("market_key") or market.market_key),
        time=observed,
        timeframe=str(row.get("timeframe") or spec.timeframe or "1m"),
        open=_decimal(row.get("open")),
        high=_decimal(row.get("high")),
        low=_decimal(row.get("low")),
        close=_decimal(row.get("close")),
        volume=_decimal(row.get("volume")),
        source=str(row.get("venue") or market.venue),
    )


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _storage_spec(spec: MarketDataSpec) -> MarketDataSpec:
    return MarketDataSpec(
        spec.symbol,
        spec.kind,
        venue=spec.venue,
        market=spec.market,
        timeframe=spec.timeframe,
        start=_storage_time(spec.start),
        # User-facing date windows are inclusive. The catalog query is
        # half-open, so include the entire requested end date.
        end=_storage_end(spec.end),
        limit=spec.limit,
        dataset=spec.dataset,
        stream=spec.stream,
        provider=spec.provider,
    )


def _storage_time(value: MarketTime | None) -> MarketTime | None:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return value


def _storage_end(value: MarketTime | None) -> MarketTime | None:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc) + timedelta(days=1)
    return _storage_time(value)


def _historical_status_key(operations: MarketDataOperationsService, spec: MarketDataSpec) -> str:
    resolved = operations.resolve(spec)
    return "market.warmup." + "|".join((
        str(resolved.dataset_id),
        str(spec.start or ""),
        str(spec.end or ""),
        str(spec.limit or ""),
    ))


def _read_metadata(store: MarketDataStore, key: str) -> MarketWarmupStatus | None:
    return store.read_metadata(key)


def _write_metadata(store: MarketDataStore, key: str, value: MarketWarmupStatus) -> None:
    store.write_metadata(key, value)


def _historical_status(
    state: str,
    *,
    cooldown: float,
    error_type: str | None = None,
) -> MarketWarmupStatus:
    retry_at = datetime.now(timezone.utc).timestamp() + cooldown
    result: MarketWarmupStatus = {
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "retry_at": retry_at,
    }
    if error_type is not None:
        result["error_type"] = error_type
    return result


def _status_is_cooling_down(status: MarketWarmupStatus | None) -> bool:
    if not status or status.get("state") not in {"empty", "failed"}:
        return False
    try:
        return float(status.get("retry_at", 0)) > datetime.now(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return False

__all__ = [
    "DataSubscription",
    "DataSubscriptionGroup",
    "MarketDataApplicationService",
    "MarketDataSpec",
    "MarketDataSubscriptionSpec",
    "MarketDataSubscriptionGroupSpec",
    "MarketPartition",
    "parse_market_dataset_id",
    "ResolvedMarketData",
]
