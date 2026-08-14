"""Direct read-only SQLite capability for the Reference contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Any


@dataclass(frozen=True, slots=True)
class ReferenceWatermark:
    generation: int
    event_sequence: int
    committed_at_unix_nanos: int


@dataclass(frozen=True, slots=True)
class ReferenceMarket:
    market_id: str
    market_key: str
    instrument_id: str
    listing_id: str
    exchange_id: str
    market_type: str
    source_symbol: str
    status: str


class ReferenceSqliteReader:
    """Open short-lived read-only SQLite connections; never mmap/view backed."""

    def __init__(self, database_path: Path, *, timeout: float = 5.0) -> None:
        self.database_path = Path(database_path)
        self.timeout = timeout

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.database_path}?mode=ro",
            uri=True,
            timeout=self.timeout,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def watermark(self) -> ReferenceWatermark:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT generation,event_sequence,committed_at_unix_nanos "
                "FROM reference_meta WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Reference SQLite metadata is missing")
        return ReferenceWatermark(int(row[0]), int(row[1]), int(row[2]))

    def market(self, market_id: str) -> ReferenceMarket | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM reference_markets_current WHERE market_id = ?",
                (market_id,),
            ).fetchone()
        return None if row is None else _market(json.loads(str(row[0])))

    def markets(self, *, limit: int = 10_000) -> tuple[ReferenceMarket, ...]:
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM reference_markets_current "
                "ORDER BY market_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_market(json.loads(str(row[0]))) for row in rows)


def _market(value: dict[str, Any]) -> ReferenceMarket:
    required = (
        "market_id",
        "market_key",
        "instrument_id",
        "listing_id",
        "exchange_id",
        "market_type",
        "source_symbol",
        "status",
    )
    missing = [name for name in required if not isinstance(value.get(name), str)]
    if missing:
        raise ValueError(f"Reference market payload is missing: {', '.join(missing)}")
    return ReferenceMarket(*(str(value[name]) for name in required))


__all__ = ["ReferenceMarket", "ReferenceSqliteReader", "ReferenceWatermark"]
