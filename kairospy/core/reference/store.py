from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping

from .catalog import ReferenceCatalog
from .model import LifecycleEvent
from .serde import (
    asset_from_primitive,
    asset_to_primitive,
    entity_from_primitive,
    entity_to_primitive,
    instrument_from_primitive,
    instrument_to_primitive,
    lifecycle_event_from_primitive,
    lifecycle_event_to_primitive,
    listing_from_primitive,
    listing_to_primitive,
    market_from_primitive,
    market_to_primitive,
)


@dataclass(frozen=True, slots=True)
class ReferenceStore:
    root: Path

    def __init__(self, root: str | Path = ".kairos/reference") -> None:
        object.__setattr__(self, "root", Path(root).expanduser())

    @property
    def database_path(self) -> Path:
        if self.root.suffix in {".db", ".sqlite", ".sqlite3"}:
            return self.root
        return self.root / "reference.sqlite"

    def save_catalog(self, catalog: ReferenceCatalog) -> None:
        with self._connect() as connection:
            _ensure_schema(connection)
            with connection:
                for table in _CATALOG_TABLES:
                    connection.execute(f"DELETE FROM {table}")
                _replace_catalog_rows(
                    connection,
                    "entities",
                    "entity_id",
                    (entity_to_primitive(item) for item in catalog.entities()),
                )
                _replace_catalog_rows(
                    connection,
                    "assets",
                    "asset_id",
                    (asset_to_primitive(item) for item in catalog.assets()),
                )
                _replace_catalog_rows(
                    connection,
                    "instruments",
                    "instrument_id",
                    (instrument_to_primitive(item) for item in catalog.instruments()),
                )
                _replace_catalog_rows(
                    connection,
                    "listings",
                    "listing_id",
                    (listing_to_primitive(item) for item in catalog.listings()),
                )
                _replace_catalog_rows(
                    connection,
                    "markets",
                    "market_id",
                    (market_to_primitive(item) for item in catalog.markets()),
                )

    def load_catalog(self) -> ReferenceCatalog:
        if not self.database_path.exists():
            return ReferenceCatalog()
        with self._connect() as connection:
            _ensure_schema(connection)
            return ReferenceCatalog(
                entities=(entity_from_primitive(item) for item in _load_rows(connection, "entities")),
                assets=(asset_from_primitive(item) for item in _load_rows(connection, "assets")),
                instruments=(instrument_from_primitive(item) for item in _load_rows(connection, "instruments")),
                listings=(listing_from_primitive(item) for item in _load_rows(connection, "listings")),
                markets=(market_from_primitive(item) for item in _load_rows(connection, "markets")),
            )

    def append_events(self, events: Iterable[LifecycleEvent]) -> Path:
        rows = [lifecycle_event_to_primitive(item) for item in events]
        if not rows:
            return self.database_path
        with self._connect() as connection:
            _ensure_schema(connection)
            with connection:
                connection.executemany(
                    """
                    INSERT INTO lifecycle_events (
                        event_type,
                        event_time,
                        instrument_id,
                        listing_id,
                        market_id,
                        venue,
                        source_symbol,
                        row_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            str(row.get("event_type") or ""),
                            str(row.get("event_time") or ""),
                            _optional_text(row.get("instrument_id")),
                            _optional_text(row.get("listing_id")),
                            _optional_text(row.get("market_id")),
                            _optional_text(row.get("venue")),
                            _optional_text(row.get("source_symbol")),
                            _json(row),
                        )
                        for row in rows
                    ),
                )
        return self.database_path

    def load_events(self) -> tuple[LifecycleEvent, ...]:
        if not self.database_path.exists():
            return ()
        with self._connect() as connection:
            _ensure_schema(connection)
            rows = (
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT row_json FROM lifecycle_events ORDER BY event_id"
                )
            )
            return tuple(lifecycle_event_from_primitive(item) for item in rows)

    def _connect(self) -> sqlite3.Connection:
        path = self.database_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


_CATALOG_TABLES = ("entities", "assets", "instruments", "listings", "markets")


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS entities (
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            row_json TEXT NOT NULL,
            PRIMARY KEY (entity_id, effective_from)
        );

        CREATE TABLE IF NOT EXISTS assets (
            asset_id TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            issuer_id TEXT,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            row_json TEXT NOT NULL,
            PRIMARY KEY (asset_id, effective_from)
        );

        CREATE TABLE IF NOT EXISTS instruments (
            instrument_id TEXT NOT NULL,
            instrument_type TEXT NOT NULL,
            base_asset_id TEXT,
            quote_asset_id TEXT,
            underlying_instrument_id TEXT,
            expiry TEXT,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            row_json TEXT NOT NULL,
            PRIMARY KEY (instrument_id, effective_from)
        );

        CREATE TABLE IF NOT EXISTS listings (
            listing_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            venue TEXT NOT NULL,
            trading_symbol TEXT NOT NULL,
            venue_instrument_id TEXT,
            currency_asset_id TEXT,
            status TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            row_json TEXT NOT NULL,
            PRIMARY KEY (listing_id, effective_from)
        );

        CREATE TABLE IF NOT EXISTS markets (
            market_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            listing_id TEXT NOT NULL,
            venue TEXT NOT NULL,
            market TEXT NOT NULL,
            source_symbol TEXT NOT NULL,
            status TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            row_json TEXT NOT NULL,
            PRIMARY KEY (market_id, effective_from)
        );

        CREATE TABLE IF NOT EXISTS lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            event_time TEXT NOT NULL,
            instrument_id TEXT,
            listing_id TEXT,
            market_id TEXT,
            venue TEXT,
            source_symbol TEXT,
            row_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_assets_symbol
            ON assets (symbol, effective_from, effective_to);
        CREATE INDEX IF NOT EXISTS idx_instruments_type
            ON instruments (instrument_type, effective_from, effective_to);
        CREATE INDEX IF NOT EXISTS idx_listings_venue_symbol
            ON listings (venue, trading_symbol, effective_from, effective_to);
        CREATE INDEX IF NOT EXISTS idx_markets_lookup
            ON markets (venue, market, source_symbol, status, effective_from, effective_to);
        CREATE INDEX IF NOT EXISTS idx_lifecycle_events_time
            ON lifecycle_events (event_time, venue, market_id);
        """
    )


def _replace_catalog_rows(
    connection: sqlite3.Connection,
    table: str,
    id_field: str,
    rows: Iterable[Mapping[str, object]],
) -> None:
    for row in rows:
        common = (
            _required_text(row.get(id_field), id_field),
            str(row.get("effective_from") or ""),
            _json(row),
        )
        if table == "entities":
            connection.execute(
                """
                INSERT INTO entities (
                    entity_id, entity_type, name, effective_from, effective_to, row_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    common[0],
                    _required_text(row.get("entity_type"), "entity_type"),
                    _required_text(row.get("name"), "name"),
                    common[1],
                    _optional_text(row.get("effective_to")),
                    common[2],
                ),
            )
        elif table == "assets":
            connection.execute(
                """
                INSERT INTO assets (
                    asset_id, asset_type, symbol, issuer_id, effective_from, effective_to, row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    common[0],
                    _required_text(row.get("asset_type"), "asset_type"),
                    _required_text(row.get("symbol"), "symbol"),
                    _optional_text(row.get("issuer_id")),
                    common[1],
                    _optional_text(row.get("effective_to")),
                    common[2],
                ),
            )
        elif table == "instruments":
            connection.execute(
                """
                INSERT INTO instruments (
                    instrument_id,
                    instrument_type,
                    base_asset_id,
                    quote_asset_id,
                    underlying_instrument_id,
                    expiry,
                    effective_from,
                    effective_to,
                    row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    common[0],
                    _required_text(row.get("instrument_type"), "instrument_type"),
                    _optional_text(row.get("base_asset_id")),
                    _optional_text(row.get("quote_asset_id")),
                    _optional_text(row.get("underlying_instrument_id")),
                    _optional_text(row.get("expiry")),
                    common[1],
                    _optional_text(row.get("effective_to")),
                    common[2],
                ),
            )
        elif table == "listings":
            connection.execute(
                """
                INSERT INTO listings (
                    listing_id,
                    instrument_id,
                    venue,
                    trading_symbol,
                    venue_instrument_id,
                    currency_asset_id,
                    status,
                    effective_from,
                    effective_to,
                    row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    common[0],
                    _required_text(row.get("instrument_id"), "instrument_id"),
                    _required_text(row.get("venue"), "venue"),
                    _required_text(row.get("trading_symbol"), "trading_symbol"),
                    _optional_text(row.get("venue_instrument_id")),
                    _optional_text(row.get("currency_asset_id")),
                    _required_text(row.get("status"), "status"),
                    common[1],
                    _optional_text(row.get("effective_to")),
                    common[2],
                ),
            )
        elif table == "markets":
            connection.execute(
                """
                INSERT INTO markets (
                    market_id,
                    instrument_id,
                    listing_id,
                    venue,
                    market,
                    source_symbol,
                    status,
                    effective_from,
                    effective_to,
                    row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    common[0],
                    _required_text(row.get("instrument_id"), "instrument_id"),
                    _required_text(row.get("listing_id"), "listing_id"),
                    _required_text(row.get("venue"), "venue"),
                    _required_text(row.get("market"), "market"),
                    _required_text(row.get("source_symbol"), "source_symbol"),
                    _required_text(row.get("status"), "status"),
                    common[1],
                    _optional_text(row.get("effective_to")),
                    common[2],
                ),
            )
        else:
            raise ValueError(f"unsupported reference table: {table}")


def _load_rows(connection: sqlite3.Connection, table: str) -> tuple[Mapping[str, object], ...]:
    return tuple(
        json.loads(row[0])
        for row in connection.execute(f"SELECT row_json FROM {table} ORDER BY effective_from, rowid")
    )


def _json(row: Mapping[str, object]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _required_text(value: object, name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["ReferenceStore"]
