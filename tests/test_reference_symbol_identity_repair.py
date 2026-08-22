from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "maintenance"
    / "repair_reference_symbol_identity.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("repair_reference_symbol_identity", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE reference_markets_current (
            market_id TEXT PRIMARY KEY,
            instrument_id TEXT NOT NULL,
            listing_id TEXT,
            exchange_id TEXT NOT NULL,
            instrument_kind TEXT NOT NULL,
            asset_type TEXT,
            underlying_instrument_id TEXT,
            venue_symbol TEXT,
            status TEXT NOT NULL,
            effective_to_unix_nanos INTEGER,
            payload TEXT NOT NULL
        );
        CREATE TABLE reference_listings_current (
            listing_id TEXT PRIMARY KEY,
            instrument_id TEXT NOT NULL,
            exchange_id TEXT NOT NULL,
            exchange_symbol TEXT NOT NULL,
            status TEXT NOT NULL,
            effective_to_unix_nanos INTEGER,
            payload TEXT NOT NULL
        );
        CREATE TABLE reference_provider_records (
            provider TEXT NOT NULL,
            record_kind TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(provider, record_kind, record_id)
        ) WITHOUT ROWID;
        """
    )


def _insert_listing(
    connection: sqlite3.Connection,
    listing_id: str,
    *,
    instrument_id: str,
    exchange_id: str,
    exchange_symbol: str,
    status: str = "active",
) -> None:
    payload = {
        "listing_id": listing_id,
        "instrument_id": instrument_id,
        "exchange_id": exchange_id,
        "exchange_symbol": exchange_symbol,
        "status": status,
        "effective_from_unix_nanos": 0,
        "effective_to_unix_nanos": None,
    }
    connection.execute(
        "INSERT INTO reference_listings_current"
        "(listing_id, instrument_id, exchange_id, exchange_symbol, status, "
        "effective_to_unix_nanos, payload) VALUES (?,?,?,?,?,?,?)",
        (
            listing_id,
            instrument_id,
            exchange_id,
            exchange_symbol,
            status,
            None,
            json.dumps(payload),
        ),
    )


def _insert_market(
    connection: sqlite3.Connection,
    market_id: str,
    *,
    instrument_id: str,
    listing_id: str | None,
    exchange_id: str,
    instrument_kind: str,
    venue_symbol: str,
    status: str = "active",
) -> None:
    payload = {
        "market_id": market_id,
        "instrument_id": instrument_id,
        "listing_id": listing_id,
        "exchange_id": exchange_id,
        "instrument_kind": instrument_kind,
        "asset_type": instrument_kind,
        "underlying_instrument_id": None,
        "venue_symbol": venue_symbol,
        "status": status,
        "effective_from_unix_nanos": 0,
        "effective_to_unix_nanos": None,
    }
    connection.execute(
        "INSERT INTO reference_markets_current"
        "(market_id, instrument_id, listing_id, exchange_id, instrument_kind, "
        "asset_type, underlying_instrument_id, venue_symbol, status, "
        "effective_to_unix_nanos, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            market_id,
            instrument_id,
            listing_id,
            exchange_id,
            instrument_kind,
            instrument_kind,
            None,
            venue_symbol,
            status,
            None,
            json.dumps(payload),
        ),
    )


def _insert_provider_record(
    connection: sqlite3.Connection,
    provider: str,
    record_kind: str,
    record_id: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        "INSERT INTO reference_provider_records(provider, record_kind, record_id, payload) "
        "VALUES (?,?,?,?)",
        (provider, record_kind, record_id, json.dumps(payload)),
    )


def test_reference_symbol_identity_repair_dry_run_does_not_mutate(tmp_path: Path) -> None:
    module = _load_script()
    database = _fixture_database(tmp_path)

    stats = module.repair(database, apply=False)

    assert stats.current_markets_rekeyed == 1
    with _connect(database) as connection:
        assert _count_legacy(connection) == (1, 2, 2)


def test_reference_symbol_identity_repair_apply_updates_ids_and_payloads(
    tmp_path: Path,
) -> None:
    module = _load_script()
    database = _fixture_database(tmp_path)

    backup = module.backup_database(database, tmp_path / "backups")
    stats = module.repair(database, apply=True)

    assert backup.exists()
    assert stats.provider_records_rekeyed == 2
    assert stats.current_markets_rekeyed == 1
    assert stats.current_listings_rekeyed == 1
    assert stats.current_listings_deleted == 1
    with _connect(database) as connection:
        assert _count_legacy(connection) == (0, 0, 0)
        market = connection.execute(
            "SELECT listing_id, payload FROM reference_markets_current "
            "WHERE market_id = 'market:nasdaq:equity:AAPL:USD'"
        ).fetchone()
        assert market["listing_id"] == "listing:nasdaq:equity:AAPL"
        payload = json.loads(market["payload"])
        assert payload["market_id"] == "market:nasdaq:equity:AAPL:USD"
        assert payload["listing_id"] == "listing:nasdaq:equity:AAPL"

        option_listing_count = connection.execute(
            "SELECT COUNT(*) FROM reference_listings_current "
            "WHERE listing_id = 'listing:cboe-bzx-options:option:SPY-20260821-1000-C'"
        ).fetchone()[0]
        assert option_listing_count == 1


def _fixture_database(tmp_path: Path) -> Path:
    database = tmp_path / "reference.sqlite"
    with _connect(database) as connection:
        _create_schema(connection)
        _insert_listing(
            connection,
            "listing:exchange:nasdaq:equity:AAPL:USD",
            instrument_id="instrument:equity:US:AAPL:common",
            exchange_id="exchange:nasdaq",
            exchange_symbol="AAPL",
        )
        _insert_market(
            connection,
            "market:exchange:nasdaq:equity:AAPL",
            instrument_id="instrument:equity:US:AAPL:common",
            listing_id="listing:exchange:nasdaq:equity:AAPL:USD",
            exchange_id="exchange:nasdaq",
            instrument_kind="equity",
            venue_symbol="AAPL",
        )
        _insert_listing(
            connection,
            "listing:exchange:cboe-bzx-options:option:SPY-20260821-1000-C",
            instrument_id="instrument:option:SPY:20260821:1000:C",
            exchange_id="exchange:cboe-bzx-options",
            exchange_symbol="O:SPY260821C01000000",
            status="inactive",
        )
        _insert_listing(
            connection,
            "listing:cboe-bzx-options:option:SPY-20260821-1000-C",
            instrument_id="instrument:option:SPY:20260821:1000:C",
            exchange_id="exchange:cboe-bzx-options",
            exchange_symbol="O:SPY260821C01000000",
        )
        _insert_provider_record(
            connection,
            "massive-equity",
            "listing",
            "listing:exchange:nasdaq:equity:AAPL:USD",
            {
                "listing_id": "listing:exchange:nasdaq:equity:AAPL:USD",
                "instrument_id": "instrument:equity:US:AAPL:common",
                "exchange_id": "exchange:nasdaq",
                "exchange_symbol": "AAPL",
                "status": "active",
            },
        )
        _insert_provider_record(
            connection,
            "massive-equity",
            "market",
            "market:exchange:nasdaq:equity:AAPL",
            {
                "market_id": "market:exchange:nasdaq:equity:AAPL",
                "instrument_id": "instrument:equity:US:AAPL:common",
                "listing_id": "listing:exchange:nasdaq:equity:AAPL:USD",
                "exchange_id": "exchange:nasdaq",
                "instrument_kind": "equity",
                "venue_symbol": "AAPL",
                "status": "active",
            },
        )
        connection.commit()
    return database


def _count_legacy(connection: sqlite3.Connection) -> tuple[int, int, int]:
    markets = connection.execute(
        "SELECT COUNT(*) FROM reference_markets_current "
        "WHERE market_id LIKE 'market:exchange:%'"
    ).fetchone()[0]
    listings = connection.execute(
        "SELECT COUNT(*) FROM reference_listings_current "
        "WHERE listing_id LIKE 'listing:exchange:%'"
    ).fetchone()[0]
    provider = connection.execute(
        "SELECT COUNT(*) FROM reference_provider_records "
        "WHERE record_id LIKE 'market:exchange:%' "
        "OR record_id LIKE 'listing:exchange:%'"
    ).fetchone()[0]
    return int(markets), int(listings), int(provider)
