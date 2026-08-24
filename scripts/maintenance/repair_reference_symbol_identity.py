#!/usr/bin/env python3
"""Repair legacy Reference market/listing identities in SQLite.

This is a controlled maintenance script for the Market/Symbol identity
refactor. It updates provider records and current catalog rows together so
indexed IDs and JSON payloads stay consistent. Lifecycle history is left
unchanged.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DATABASE = Path(".kairos/state/reference/reference.sqlite")


@dataclass(frozen=True)
class RepairStats:
    provider_records_rekeyed: int = 0
    provider_records_deleted: int = 0
    current_listings_rekeyed: int = 0
    current_listings_deleted: int = 0
    current_markets_rekeyed: int = 0
    current_markets_deleted: int = 0

    def __add__(self, other: "RepairStats") -> "RepairStats":
        return RepairStats(
            provider_records_rekeyed=self.provider_records_rekeyed
            + other.provider_records_rekeyed,
            provider_records_deleted=self.provider_records_deleted
            + other.provider_records_deleted,
            current_listings_rekeyed=self.current_listings_rekeyed
            + other.current_listings_rekeyed,
            current_listings_deleted=self.current_listings_deleted
            + other.current_listings_deleted,
            current_markets_rekeyed=self.current_markets_rekeyed
            + other.current_markets_rekeyed,
            current_markets_deleted=self.current_markets_deleted
            + other.current_markets_deleted,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "provider_records_rekeyed": self.provider_records_rekeyed,
            "provider_records_deleted": self.provider_records_deleted,
            "current_listings_rekeyed": self.current_listings_rekeyed,
            "current_listings_deleted": self.current_listings_deleted,
            "current_markets_rekeyed": self.current_markets_rekeyed,
            "current_markets_deleted": self.current_markets_deleted,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    database = args.database
    if not database.exists():
        raise SystemExit(f"Reference database does not exist: {database}")

    before = read_counts(database)
    backup = None
    if args.apply:
        backup = backup_database(database, args.backup_dir)
    stats = repair(database, apply=args.apply)
    after = read_counts(database)
    print(
        json.dumps(
            {
                "database": str(database),
                "applied": args.apply,
                "backup": str(backup) if backup else None,
                "before": before,
                "after": after,
                "stats": stats.as_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def open_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.row_factory = sqlite3.Row
    return connection


def backup_database(database: Path, backup_dir: Path | None) -> Path:
    backup_root = backup_dir or database.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / f"{database.stem}.symbol-identity-{stamp}.sqlite"
    with open_connection(database) as source:
        with sqlite3.connect(backup) as target:
            source.backup(target)
    return backup


def read_counts(database: Path) -> dict[str, int]:
    with open_connection(database) as connection:
        return {
            "legacy_exchange_market_ids": scalar(
                connection,
                "SELECT COUNT(*) FROM reference_markets_current "
                "WHERE market_id LIKE 'market:exchange:%'",
            ),
            "legacy_exchange_listing_ids": scalar(
                connection,
                "SELECT COUNT(*) FROM reference_listings_current "
                "WHERE listing_id LIKE 'listing:exchange:%'",
            ),
            "provider_legacy_records": scalar(
                connection,
                "SELECT COUNT(*) FROM reference_provider_records "
                "WHERE record_id LIKE 'market:exchange:%' "
                "OR record_id LIKE 'listing:exchange:%'",
            ),
            "option_markets": scalar(
                connection,
                "SELECT COUNT(*) FROM reference_markets_current "
                "WHERE instrument_kind = 'option'",
            ),
        }


def scalar(connection: sqlite3.Connection, sql: str) -> int:
    value = connection.execute(sql).fetchone()[0]
    return int(value)


def repair(database: Path, *, apply: bool) -> RepairStats:
    with open_connection(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            stats = (
                repair_provider_records(connection, apply=apply)
                + repair_current_listings(connection, apply=apply)
                + repair_current_markets(connection, apply=apply)
            )
            if apply:
                connection.commit()
            else:
                connection.rollback()
            return stats
        except Exception:
            connection.rollback()
            raise


def repair_provider_records(
    connection: sqlite3.Connection, *, apply: bool
) -> RepairStats:
    rows = connection.execute(
        "SELECT provider, record_kind, record_id, payload "
        "FROM reference_provider_records "
        "WHERE record_id LIKE 'market:exchange:%' "
        "OR record_id LIKE 'listing:exchange:%' "
        "ORDER BY provider, record_kind, record_id"
    ).fetchall()
    rekeyed = 0
    deleted = 0
    for row in rows:
        record_kind = row["record_kind"]
        payload = json.loads(row["payload"])
        new_record_id = canonical_id(row["record_id"], record_kind)
        if new_record_id is None:
            continue
        patch_payload(payload, record_kind)
        exists = provider_record_exists(
            connection, row["provider"], record_kind, new_record_id
        )
        if apply:
            if not exists:
                connection.execute(
                    "INSERT INTO reference_provider_records"
                    "(provider, record_kind, record_id, payload) VALUES (?,?,?,?)",
                    (
                        row["provider"],
                        record_kind,
                        new_record_id,
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
                rekeyed += 1
            else:
                deleted += 1
            connection.execute(
                "DELETE FROM reference_provider_records "
                "WHERE provider = ? AND record_kind = ? AND record_id = ?",
                (row["provider"], record_kind, row["record_id"]),
            )
        elif exists:
            deleted += 1
        else:
            rekeyed += 1
    return RepairStats(
        provider_records_rekeyed=rekeyed,
        provider_records_deleted=deleted,
    )


def provider_record_exists(
    connection: sqlite3.Connection, provider: str, record_kind: str, record_id: str
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM reference_provider_records "
            "WHERE provider = ? AND record_kind = ? AND record_id = ?",
            (provider, record_kind, record_id),
        ).fetchone()
        is not None
    )


def repair_current_listings(
    connection: sqlite3.Connection, *, apply: bool
) -> RepairStats:
    rows = connection.execute(
        "SELECT listing_id, instrument_id, exchange_id, exchange_symbol, status, "
        "effective_to_unix_nanos, payload "
        "FROM reference_listings_current "
        "WHERE listing_id LIKE 'listing:exchange:%' "
        "ORDER BY listing_id"
    ).fetchall()
    rekeyed = 0
    deleted = 0
    for row in rows:
        payload = json.loads(row["payload"])
        new_listing_id = canonical_id(row["listing_id"], "listing")
        if new_listing_id is None:
            continue
        payload["listing_id"] = new_listing_id
        exists = current_listing_exists(connection, new_listing_id)
        if apply:
            if not exists:
                connection.execute(
                    "INSERT INTO reference_listings_current"
                    "(listing_id, instrument_id, exchange_id, exchange_symbol, status, "
                    "effective_to_unix_nanos, payload) VALUES (?,?,?,?,?,?,?)",
                    (
                        new_listing_id,
                        row["instrument_id"],
                        row["exchange_id"],
                        row["exchange_symbol"],
                        row["status"],
                        row["effective_to_unix_nanos"],
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
                rekeyed += 1
            else:
                deleted += 1
            connection.execute(
                "DELETE FROM reference_listings_current WHERE listing_id = ?",
                (row["listing_id"],),
            )
        elif exists:
            deleted += 1
        else:
            rekeyed += 1
    return RepairStats(
        current_listings_rekeyed=rekeyed,
        current_listings_deleted=deleted,
    )


def current_listing_exists(connection: sqlite3.Connection, listing_id: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM reference_listings_current WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
        is not None
    )


def repair_current_markets(
    connection: sqlite3.Connection, *, apply: bool
) -> RepairStats:
    rows = connection.execute(
        "SELECT market_id, instrument_id, listing_id, exchange_id, instrument_kind, "
        "asset_type, underlying_instrument_id, venue_symbol, status, "
        "effective_to_unix_nanos, payload "
        "FROM reference_markets_current "
        "WHERE market_id LIKE 'market:exchange:%' "
        "ORDER BY market_id"
    ).fetchall()
    rekeyed = 0
    deleted = 0
    for row in rows:
        payload = json.loads(row["payload"])
        new_market_id = canonical_id(row["market_id"], "market")
        if new_market_id is None:
            continue
        new_listing_id = (
            canonical_id(row["listing_id"], "listing") if row["listing_id"] else None
        )
        payload["market_id"] = new_market_id
        if new_listing_id is not None:
            payload["listing_id"] = new_listing_id
        exists = current_market_exists(connection, new_market_id)
        if apply:
            if not exists:
                connection.execute(
                    "INSERT INTO reference_markets_current"
                    "(market_id, instrument_id, listing_id, exchange_id, instrument_kind, "
                    "asset_type, underlying_instrument_id, venue_symbol, status, "
                    "effective_to_unix_nanos, payload) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_market_id,
                        row["instrument_id"],
                        new_listing_id,
                        row["exchange_id"],
                        row["instrument_kind"],
                        row["asset_type"],
                        row["underlying_instrument_id"],
                        row["venue_symbol"],
                        row["status"],
                        row["effective_to_unix_nanos"],
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
                rekeyed += 1
            else:
                deleted += 1
            connection.execute(
                "DELETE FROM reference_markets_current WHERE market_id = ?",
                (row["market_id"],),
            )
        elif exists:
            deleted += 1
        else:
            rekeyed += 1
    return RepairStats(
        current_markets_rekeyed=rekeyed,
        current_markets_deleted=deleted,
    )


def current_market_exists(connection: sqlite3.Connection, market_id: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM reference_markets_current WHERE market_id = ?",
            (market_id,),
        ).fetchone()
        is not None
    )


def canonical_id(value: str | None, kind: str) -> str | None:
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) < 5 or parts[1] != "exchange":
        return None
    exchange = parts[2]
    instrument_kind = parts[3]
    if kind == "listing" and parts[0] == "listing":
        key_parts = parts[4:]
        if instrument_kind == "equity" and len(key_parts) >= 2 and key_parts[-1] == "USD":
            key_parts = key_parts[:-1]
        return f"listing:{exchange}:{instrument_kind}:{':'.join(key_parts)}"
    if kind == "market" and parts[0] == "market":
        key_parts = parts[4:]
        if instrument_kind == "equity" and len(key_parts) == 1:
            key_parts = [key_parts[0], "USD"]
        return f"market:{exchange}:{instrument_kind}:{':'.join(key_parts)}"
    return None


def patch_payload(payload: dict[str, Any], record_kind: str) -> None:
    if record_kind == "listing":
        payload["listing_id"] = canonical_id(payload.get("listing_id"), "listing")
    elif record_kind == "market":
        payload["market_id"] = canonical_id(payload.get("market_id"), "market")
        if payload.get("listing_id") is not None:
            payload["listing_id"] = canonical_id(payload.get("listing_id"), "listing")


if __name__ == "__main__":
    raise SystemExit(main())
