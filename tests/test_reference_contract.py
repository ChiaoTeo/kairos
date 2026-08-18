from __future__ import annotations

import asyncio
import json
from pathlib import Path
from io import StringIO
import sqlite3
from types import SimpleNamespace

import pytest

from kairospy.application.reference import (
    Asset,
    Instrument,
    Listing,
    ReferenceApplication,
    ReferenceNotFoundError,
    validate_reference_runtime,
)
from kairospy.domain_types import InstrumentId, ListingId, MarketId
from kairospy.infrastructure.contracts.reference import ReferenceClient
from kairospy.surface.cli.commands.reference import _observe_reference_stream


def _reference_database(tmp_path: Path) -> Path:
    path = tmp_path / "reference.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE reference_meta(
            id INTEGER PRIMARY KEY, generation INTEGER, event_sequence INTEGER
        );
        INSERT INTO reference_meta VALUES(1, 3, 7);
        CREATE TABLE reference_entities_current(
            entity_id TEXT PRIMARY KEY, entity_type TEXT, status TEXT, payload TEXT
        );
        CREATE TABLE reference_assets_current(
            asset_id TEXT PRIMARY KEY, code TEXT, asset_class TEXT, status TEXT,
            payload TEXT
        );
        CREATE TABLE reference_instruments_current(
            instrument_id TEXT PRIMARY KEY, symbol TEXT, instrument_type TEXT,
            product_family TEXT, underlying_instrument_id TEXT,
            expiry_unix_nanos INTEGER, status TEXT, payload TEXT
        );
        CREATE TABLE reference_listings_current(
            listing_id TEXT PRIMARY KEY, instrument_id TEXT, exchange_id TEXT,
            exchange_symbol TEXT, status TEXT, payload TEXT
        );
        CREATE TABLE reference_markets_current(
            market_id TEXT PRIMARY KEY, instrument_id TEXT, listing_id TEXT,
            exchange_id TEXT, instrument_kind TEXT, asset_type TEXT,
            underlying_instrument_id TEXT, venue_symbol TEXT,
            status TEXT, effective_to_unix_nanos INTEGER,
            payload TEXT
        );
        CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);
        CREATE TABLE reference_option_coverage(
            provider TEXT, underlying TEXT, enabled INTEGER
        );
        """
    )
    market = {
        "market_id": "market:binance:spot:BTCUSDT",
        "instrument_id": "instrument:spot:BTC",
        "listing_id": "listing:binance:spot:BTCUSDT",
        "exchange_id": "exchange:binance",
        "instrument_kind": "spot",
        "asset_type": None,
        "venue_symbol": "BTCUSDT",
        "status": "active",
    }
    connection.execute(
        "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            market["market_id"],
            market["instrument_id"],
            market["listing_id"],
            market["exchange_id"],
            market["instrument_kind"],
            market["asset_type"],
            None,
            market["venue_symbol"],
            market["status"],
            None,
            json.dumps(market),
        ),
    )
    records = {
        "reference_entities_current": (
            ("entity_id", "entity_type", "status"),
            {
                "entity_id": "exchange:binance",
                "entity_type": "exchange",
                "name": "Binance",
                "status": "active",
            },
        ),
        "reference_assets_current": (
            ("asset_id", "code", "asset_class", "status"),
            {
                "asset_id": "asset:crypto:BTC",
                "code": "BTC",
                "name": "Bitcoin",
                "asset_class": "crypto",
                "status": "active",
            },
        ),
        "reference_instruments_current": (
            (
                "instrument_id",
                "symbol",
                "instrument_type",
                "product_family",
                "underlying_instrument_id",
                "expiry_unix_nanos",
                "status",
            ),
            {
                "instrument_id": "instrument:spot:BTC",
                "symbol": "BTC",
                "name": "Bitcoin spot",
                "instrument_type": "spot",
                "product_family": "spot",
                "underlying_instrument_id": None,
                "expiry_unix_nanos": None,
                "status": "active",
            },
        ),
        "reference_listings_current": (
            (
                "listing_id",
                "instrument_id",
                "exchange_id",
                "exchange_symbol",
                "status",
            ),
            {
                "listing_id": "listing:binance:spot:BTCUSDT",
                "instrument_id": "instrument:spot:BTC",
                "exchange_id": "exchange:binance",
                "exchange_symbol": "BTCUSDT",
                "status": "active",
                "effective_from_unix_nanos": 0,
                "effective_to_unix_nanos": None,
            },
        ),
    }
    for table, (columns, payload) in records.items():
        connection.execute(
            f"INSERT INTO {table}({','.join(columns)},payload) "
            f"VALUES({','.join('?' for _ in range(len(columns) + 1))})",
            (*(payload.get(column) for column in columns), json.dumps(payload)),
        )
    option = {
        "instrument_id": "instrument:option:BTC:2030:C:100000",
        "symbol": "BTC2030C100000",
        "name": None,
        "instrument_type": "option",
        "product_family": "option",
        "underlying_instrument_id": "instrument:spot:BTC",
        "expiry_unix_nanos": 1_900_000_000_000_000_000,
        "strike": "100000",
        "option_right": "call",
        "status": "active",
    }
    connection.execute(
        "INSERT INTO reference_instruments_current VALUES(?,?,?,?,?,?,?,?)",
        (
            option["instrument_id"],
            option["symbol"],
            option["instrument_type"],
            option["product_family"],
            option["underlying_instrument_id"],
            option["expiry_unix_nanos"],
            option["status"],
            json.dumps(option),
        ),
    )
    connection.commit()
    connection.close()
    return path


def test_reference_sqlite_client_reads_watermark_and_scoped_markets(tmp_path) -> None:
    client = ReferenceClient(database_path=_reference_database(tmp_path))
    assert client.catalog()["generation"] == 3
    assert client.catalog()["catalog"]["market_count"] == 1
    assert (
        client.resolve_market(symbol="BTCUSDT")["instrument_id"]
        == "instrument:spot:BTC"
    )
    assert (
        len(
            client.markets(
                instrument_id="instrument:spot:BTC",
                listing_id="listing:binance:spot:BTCUSDT",
                symbol="BTCUSDT",
                active_only=True,
            )
        )
        == 1
    )
    assert len(client.assets(code="BTC", asset_class="crypto")) == 1
    assert len(client.entities(entity_type="exchange", active_only=True)) == 1
    assert (
        len(
            client.listings(
                instrument_id="instrument:spot:BTC",
                exchange_id="binance",
                exchange_symbol="BTCUSDT",
            )
        )
        == 1
    )


def test_reference_application_reads_concrete_sqlite_client(tmp_path) -> None:
    application = ReferenceApplication(
        ReferenceClient(database_path=_reference_database(tmp_path))
    )

    markets = application.find_markets(
        symbol="BTCUSDT", exchange="binance", instrument_kind="spot"
    )

    assert len(markets) == 1
    assert markets[0].id == MarketId("market:binance:spot:BTCUSDT")
    assert markets[0].venue_symbol == "BTCUSDT"
    assert application.require_market(markets[0].id) == markets[0]
    assert application.market(MarketId("market:missing")) is None
    with pytest.raises(ReferenceNotFoundError):
        application.require_market(
            symbol="ETHUSDT", exchange="binance", instrument_kind="spot"
        )


def test_reference_application_exposes_typed_catalog_and_access_queries(
    tmp_path,
) -> None:
    application = ReferenceApplication(
        ReferenceClient(database_path=_reference_database(tmp_path))
    )
    market_id = MarketId("market:binance:spot:BTCUSDT")

    assert application.require_entity("exchange:binance").name == "Binance"
    assert isinstance(application.require_asset("asset:crypto:BTC"), Asset)
    instrument = application.require_instrument(InstrumentId("instrument:spot:BTC"))
    assert isinstance(instrument, Instrument)
    assert instrument.ref.display_symbol == "BTC"
    assert isinstance(
        application.require_listing(ListingId("listing:binance:spot:BTCUSDT")),
        Listing,
    )
    assert application.require_market(market_id).venue_symbol == "BTCUSDT"


def test_reference_application_reads_option_chain_and_batch_ids(tmp_path) -> None:
    application = ReferenceApplication(
        ReferenceClient(database_path=_reference_database(tmp_path))
    )

    chain = application.option_chain(
        InstrumentId("instrument:spot:BTC"), option_right="call"
    )
    instruments = application.find_instruments(
        instrument_ids=(
            "instrument:spot:BTC",
            "instrument:option:BTC:2030:C:100000",
        )
    )

    assert [str(value.id) for value in chain] == ["instrument:option:BTC:2030:C:100000"]
    assert len(instruments) == 2
    assert chain[0].strike is not None and str(chain[0].strike) == "100000"


def test_reference_application_snapshot_pins_generation_and_rows(tmp_path) -> None:
    database = _reference_database(tmp_path)
    application = ReferenceApplication(ReferenceClient(database_path=database))

    with application.snapshot() as snapshot:
        assert (snapshot.generation, snapshot.event_sequence) == (3, 7)
        before = snapshot.require_asset("asset:crypto:BTC")
        writer = sqlite3.connect(database)
        writer.execute(
            "UPDATE reference_meta SET generation = 4, event_sequence = 8 WHERE id = 1"
        )
        writer.execute(
            "UPDATE reference_assets_current SET code = 'XBT', "
            "payload = json_set(payload, '$.code', 'XBT') "
            "WHERE asset_id = 'asset:crypto:BTC'"
        )
        writer.commit()
        writer.close()
        after = snapshot.require_asset("asset:crypto:BTC")
        assert after == before
        assert (snapshot.generation, snapshot.event_sequence) == (3, 7)

    assert application.require_asset("asset:crypto:BTC").code == "XBT"
    with application.snapshot() as latest:
        assert (latest.generation, latest.event_sequence) == (4, 8)


def test_market_id_lookup_is_not_truncated_by_catalog_size(tmp_path) -> None:
    database = _reference_database(tmp_path)
    connection = sqlite3.connect(database)
    template = {
        "instrument_id": "instrument:spot:BTC",
        "listing_id": "listing:binance:spot:BTCUSDT",
        "exchange_id": "exchange:binance",
        "instrument_kind": "spot",
        "asset_type": "crypto",
        "venue_symbol": "TEST",
        "status": "active",
    }
    rows = []
    for index in range(10_001):
        market_id = f"market:test:{index:05d}"
        payload = {**template, "market_id": market_id}
        rows.append(
            (
                market_id,
                payload["instrument_id"],
                payload["listing_id"],
                payload["exchange_id"],
                payload["instrument_kind"],
                payload["asset_type"],
                None,
                payload["venue_symbol"],
                payload["status"],
                None,
                json.dumps(payload),
            )
        )
    connection.executemany(
        "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()

    application = ReferenceApplication(ReferenceClient(database_path=database))
    target = MarketId("market:test:10000")
    assert application.require_market(target).id == target


def test_reference_catalog_golden_fixture_has_cross_language_shape() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "reference_catalog_empty.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(fixture) == {
        "entities",
        "assets",
        "instruments",
        "listings",
        "markets",
        "lifecycle_events",
        "generation",
        "event_sequence",
    }


def test_reference_client_reads_lifecycle_events_by_sequence(
    tmp_path,
) -> None:
    client = ReferenceClient(database_path=_reference_database(tmp_path))
    result = client.events(sequence_from=4, sequence_to=8, limit=9)

    assert result["event_sequence"] == 7
    assert result["events"] == []


def test_reference_client_scopes_refresh_and_provider_controls(
    tmp_path, monkeypatch
) -> None:
    observed: list[tuple[str, str, float]] = []

    def request_sync(socket_path, method, target, body=None, *, timeout):
        assert socket_path == tmp_path / "reference.sock"
        observed.append((method, target, timeout))
        return 200, {"status": "ok"}

    monkeypatch.setattr(
        "kairospy.infrastructure.transport.commands.request_sync", request_sync
    )
    client = ReferenceClient(
        socket_path=tmp_path / "reference.sock",
        database_path=_reference_database(tmp_path),
    )

    client.refresh(source="massive-options")
    client.set_source_paused("massive-options", True)
    client.set_source_paused("massive-options", False)
    client.option_coverage()
    client.set_option_underlying("SPY", True)
    client.set_option_underlying("SPY", False)

    assert observed == [
        ("POST", "/v1/refresh?source=massive-options", 120.0),
        ("POST", "/v1/sources/pause?source=massive-options", 5.0),
        ("POST", "/v1/sources/resume?source=massive-options", 5.0),
        ("POST", "/v1/options/coverage/add?underlying=SPY", 120.0),
        ("POST", "/v1/options/coverage/remove?underlying=SPY", 120.0),
    ]


def test_reference_client_pages_filtered_collections(tmp_path) -> None:
    client = ReferenceClient(database_path=_reference_database(tmp_path))

    first = client.instruments(active_only=True, limit=1, offset=0)
    second = client.instruments(active_only=True, limit=1, offset=1)

    assert len(first) == len(second) == 1
    assert first[0]["instrumentId"] != second[0]["instrumentId"]
    with pytest.raises(ValueError, match="limit must be between"):
        client.instruments(limit=10_001)
    with pytest.raises(ValueError, match="offset must be non-negative"):
        client.instruments(offset=-1)
    with pytest.raises(ValueError, match="must not exceed"):
        client.instruments(
            expiry_from_unix_nanos=20,
            expiry_to_unix_nanos=10,
        )
    with pytest.raises(ValueError, match="call or put"):
        client.instruments(option_right="unknown")


def test_reference_query_cli_exposes_filtered_markets_and_option_chain(
    tmp_path, monkeypatch
) -> None:
    client = ReferenceClient(database_path=_reference_database(tmp_path))
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.reference._client", lambda workspace: client
    )
    from kairospy.surface.cli.app import execute_argv

    market_output = StringIO()
    assert (
        execute_argv(
            [
                "reference",
                "markets",
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--active-only",
                "--limit",
                "1",
                "--format",
                "json",
            ],
            market_output,
        )
        == 0
    )
    assert json.loads(market_output.getvalue())[0]["venue_symbol"] == "BTCUSDT"

    chain_output = StringIO()
    assert (
        execute_argv(
            [
                "reference",
                "option-chain",
                "--underlying",
                "instrument:spot:BTC",
                "--option-right",
                "call",
                "--format",
                "json",
            ],
            chain_output,
        )
        == 0
    )
    assert json.loads(chain_output.getvalue())[0]["instrumentType"] == "option"


def test_reference_runtime_validation_covers_provider_snapshot_and_event_tail() -> None:
    class Client(ReferenceClient):
        def health(self):
            return {
                "status": "ready",
                "generation": 3,
                "event_sequence": 7,
                "market_count": 2,
                "outbox_depth": 0,
                "providers": [
                    {"source_id": "provider-a", "status": "ready", "stale": False}
                ],
            }

        def catalog(self):
            return {
                "generation": 3,
                "event_sequence": 7,
                "catalog": {"market_count": 2},
            }

        def events(self, *, sequence_from=None, sequence_to=None, limit=256):
            assert (sequence_from, sequence_to, limit) == (7, None, 1)
            return {
                "generation": 3,
                "event_sequence": 7,
                "events": [{"event_id": "reference:00000000000000000007"}],
            }

    result = validate_reference_runtime(Client(), required_sources=("provider-a",))

    assert result["status"] == "passed"
    assert result["failed_checks"] == []
    assert len(result["checks"]) == 6


def test_reference_runtime_validation_reports_missing_provider_and_pending_outbox() -> (
    None
):
    class Client(ReferenceClient):
        def health(self):
            return {
                "status": "degraded",
                "generation": 1,
                "event_sequence": 0,
                "market_count": 0,
                "outbox_depth": 2,
                "providers": [],
            }

        def catalog(self):
            return {
                "generation": 1,
                "event_sequence": 0,
                "catalog": {"market_count": 0},
            }

        def events(self, **kwargs):
            raise AssertionError("zero watermark must not query the event tail")

    result = validate_reference_runtime(Client(), required_sources=("provider-a",))

    assert result["status"] == "failed"
    assert "required_providers_ready" in result["failed_checks"]
    assert "publication_outbox_drained" in result["failed_checks"]


def test_reference_validate_cli_returns_nonzero_when_a_required_gate_fails(
    monkeypatch,
) -> None:
    class Client(ReferenceClient):
        def health(self):
            return {
                "status": "degraded",
                "generation": 0,
                "event_sequence": 0,
                "market_count": 0,
                "outbox_depth": 1,
                "providers": [],
            }

        def catalog(self):
            return {
                "generation": 0,
                "event_sequence": 0,
                "catalog": {"market_count": 0},
            }

        def events(self, **kwargs):
            raise AssertionError("zero watermark must not query the event tail")

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.reference._client", lambda workspace: Client()
    )
    from kairospy.surface.cli.app import execute_argv

    output = StringIO()
    exit_code = execute_argv(["reference", "validate", "--output", "json"], output)

    assert exit_code == 1
    assert '"status": "failed"' in output.getvalue()


class _ReferenceEventSource:
    def __init__(
        self, events: tuple[SimpleNamespace, ...], *, remain_open: bool
    ) -> None:
        self.events = events
        self.remain_open = remain_open
        self.closed = False

    async def subscribe_live(self):
        for event in self.events:
            yield event
        if self.remain_open:
            await asyncio.sleep(60)

    async def close(self) -> None:
        self.closed = True


def test_reference_stream_observer_uses_native_events_and_stops_when_idle() -> None:
    source = _ReferenceEventSource(
        (
            SimpleNamespace(event_id="event-1", catalog_revision=4, sequence=8),
            SimpleNamespace(event_id="event-2", catalog_revision=5, sequence=9),
        ),
        remain_open=True,
    )

    result = asyncio.run(
        _observe_reference_stream(
            source,
            timeout_seconds=1,
            idle_timeout_seconds=0.01,
        )
    )

    assert result == {
        "status": "received",
        "batches": 2,
        "events": 2,
        "generation": 5,
        "event_sequence": 9,
        "first_event_id": "event-1",
        "last_event_id": "event-2",
    }
    assert source.closed


def test_reference_stream_observer_rejects_an_empty_stream() -> None:
    source = _ReferenceEventSource((), remain_open=True)

    with pytest.raises(RuntimeError, match="before timeout"):
        asyncio.run(
            _observe_reference_stream(
                source,
                timeout_seconds=0.01,
                idle_timeout_seconds=0.01,
            )
        )

    assert source.closed
