from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.domain.market import Bar, Quote
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.persistence.application.market_data import DataStore


class _Client:
    def bars(self, symbol, *, timeframe, since, until, limit):
        yield Bar(
            instrument_id="instrument:option:spy",
            market_id="market:massive:option:spy",
            market_key="massive_option_spy",
            time=datetime(2026, 1, 2, tzinfo=timezone.utc),
            timeframe=timeframe,
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("1.5"),
            volume=Decimal("10"),
            source="massive",
        )


class _CountingClient(_Client):
    def __init__(self) -> None:
        self.calls = 0

    def bars(self, symbol, *, timeframe, since, until, limit, adapter_options=None):
        self.calls += 1
        return super().bars(symbol, timeframe=timeframe, since=since, until=until, limit=limit)


class _EmptyClient:
    def bars(self, symbol, *, timeframe, since, until, limit, adapter_options=None):
        return ()


class _CountingEmptyClient(_EmptyClient):
    def __init__(self) -> None:
        self.calls = 0

    def bars(self, symbol, *, timeframe, since, until, limit, adapter_options=None):
        self.calls += 1
        return super().bars(symbol, timeframe=timeframe, since=since, until=until, limit=limit, adapter_options=adapter_options)


class _FailingClient:
    def bars(self, symbol, *, timeframe, since, until, limit):
        raise ConnectionError("remote endpoint reset")


def test_live_market_warmup_produces_normalized_market_messages_before_stream() -> None:
    source = build_live_market(
        source=object(),
        source_name="warmup-test",
        warmup_specs=(MarketDataSpec(
            symbol="O:SPY260821P00500000",
            kind="ohlcv",
            venue="massive",
            market="option",
            timeframe="1m",
            start="2026-01-01",
            end="2026-01-03",
        ),),
        warmup_client_factory=lambda spec: _Client(),
    )

    messages = source.warmup_events()

    assert len(messages) == 1
    assert messages[0].topic == "market.bar"
    assert messages[0].payload.value.close == Decimal("1.5")
    assert messages[0].payload.source == "historical"


def test_warmup_is_derived_from_strategy_subscription_after_on_start() -> None:
    source = build_live_market(source=object(), source_name="subscription-warmup", warmup_client_factory=lambda spec: _Client())
    service = MarketApplication()
    source.set_market_service(service)
    source.subscribe(MarketDataSubscriptionSpec(
        MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000"),
        (Bar.select(interval="1m"),),
        identity="strategy",
        params={"history_start": "2026-01-01", "history_end": "2026-01-03"},
    ))

    assert len(source.warmup_events()) == 1


def test_quote_subscription_can_declare_historical_timeframe_without_streaming_bars() -> None:
    source = build_live_market(source=object(), source_name="quote-history-warmup", warmup_client_factory=lambda spec: _Client())
    service = MarketApplication()
    source.set_market_service(service)
    source.subscribe(MarketDataSubscriptionSpec(
        MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000"),
        (Quote.select(),),
        identity="strategy",
        params={"history_start": "2026-01-01", "history_end": "2026-01-03", "history_timeframe": "1d"},
    ))

    assert len(source.warmup_events()) == 1


def test_warmup_reports_progress_and_honors_stop_request() -> None:
    source = build_live_market(
        source=object(),
        source_name="stopped-warmup",
        warmup_specs=(MarketDataSpec(
            symbol="SPY",
            kind="ohlcv",
            venue="massive",
            market="equity",
            timeframe="1d",
            start="2026-01-01",
            end="2026-01-03",
        ),),
        warmup_client_factory=lambda spec: _Client(),
    )
    progress = []

    messages = source.warmup_events(
        stop_requested=lambda: True,
        progress=lambda index, total, spec, state: progress.append((index, total, spec.symbol, state)),
    )

    assert messages == ()
    assert progress == [(1, 1, "SPY", "stopped")]


def test_warmup_isolates_network_failure_per_contract() -> None:
    source = build_live_market(
        source=object(),
        source_name="degraded-warmup",
        warmup_specs=(
            MarketDataSpec("O:SPY260821P00500000", "ohlcv", venue="massive", market="option"),
            MarketDataSpec("SPY", "ohlcv", venue="massive", market="equity"),
        ),
        warmup_client_factory=lambda spec: _FailingClient() if spec.symbol.startswith("O:") else _Client(),
    )
    progress = []

    messages = source.warmup_events(progress=lambda index, total, spec, state: progress.append((spec.symbol, state)))

    assert len(messages) == 1
    assert progress[0] == ("O:SPY260821P00500000", "checking")
    assert progress[1] == ("O:SPY260821P00500000", "failed")
    assert progress[2] == ("SPY", "checking")
    assert progress[3] == ("SPY", "ready")
    assert progress[-1][1] == "degraded failed=1"


def test_market_usecase_persists_warmup_and_reuses_it(tmp_path) -> None:
    store = DataStore(tmp_path / "data", storage_format="jsonl")
    application = MarketApplication(store=store)
    spec = MarketDataSpec(
        symbol="SPY",
        kind="ohlcv",
        venue="massive",
        market="equity",
        timeframe="1d",
        start="2026-01-01",
        end="2026-01-03",
    )
    client = _CountingClient()

    first = application.ensure_bars(spec, client)
    second = application.ensure_bars(spec, client)

    assert len(first) == len(second) == 1
    assert client.calls == 1


def test_market_usecase_treats_empty_history_as_a_valid_result(tmp_path) -> None:
    application = MarketApplication(store=DataStore(tmp_path / "data", storage_format="jsonl"))
    spec = MarketDataSpec(
        symbol="O:SPY260804C00540000",
        kind="ohlcv",
        venue="massive",
        market="option",
        timeframe="1d",
        start="2026-01-01",
        end="2026-01-03",
    )

    assert application.ensure_bars(spec, _EmptyClient()) == ()


def test_market_usecase_persists_empty_history_cooldown_across_restart(tmp_path) -> None:
    store_root = tmp_path / "data"
    spec = MarketDataSpec(
        symbol="O:SPY260804C00540000",
        kind="ohlcv",
        venue="massive",
        market="option",
        timeframe="1d",
        start="2026-01-01",
        end="2026-01-03",
    )
    first_client = _CountingEmptyClient()
    second_client = _CountingEmptyClient()

    MarketApplication(store=DataStore(store_root, storage_format="jsonl")).ensure_bars(spec, first_client)
    result = MarketApplication(store=DataStore(store_root, storage_format="jsonl")).ensure_bars(spec, second_client)

    assert result == ()
    assert first_client.calls == 1
    assert second_client.calls == 0
