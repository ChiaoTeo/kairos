#!/usr/bin/env python3
"""Report legacy provider-as-market identities without rewriting history.

The report is deliberately read-only.  A legacy ID is not renamed because the
correct replacement may be a canonical venue Market, a consolidated scope, or
no Market at all.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


LEGACY_PREFIXES = (
    "market:massive:",
    "listing:massive:",
    "market:binance:equity:",
    "listing:binance:equity:",
    "market:opra",
)


def classify(identifier: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    instrument_id = None if payload is None else payload.get("instrument_id")
    if identifier.startswith("market:binance:equity:"):
        action = "remove_market_keep_instrument_and_execution_route"
    elif identifier.startswith("listing:binance:equity:"):
        action = "remove_listing_rebuild_only_from_authoritative_listing_venue"
    elif identifier.startswith("listing:massive:"):
        action = "remove_listing_rebuild_from_primary_exchange_fact"
    elif identifier.startswith(("market:massive:", "market:opra")):
        action = (
            "migrate_observation_to_consolidated_scope"
            if isinstance(instrument_id, str) and instrument_id.strip()
            else "manual_review_missing_instrument_id"
        )
    else:
        action = "manual_review"
    return {
        "legacy_id": identifier,
        "instrument_id": instrument_id,
        "recommended_action": action,
    }


def walk(value: Any) -> Iterable[tuple[str, dict[str, Any] | None]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"market_id", "listing_id"} and isinstance(item, str):
                if item.startswith(LEGACY_PREFIXES):
                    yield item, value
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def scan_jsonl(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            findings.append(
                {
                    "path": str(path),
                    "line": number,
                    "recommended_action": "manual_review_invalid_json",
                    "error": str(error),
                }
            )
            continue
        for identifier, payload in walk(value):
            findings.append(
                {"path": str(path), "line": number, **classify(identifier, payload)}
            )
    return findings


def scan_sqlite(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, column in (
            ("reference_markets_current", "market_id"),
            ("reference_listings_current", "listing_id"),
        ):
            if table not in tables:
                continue
            for identifier, payload_json in connection.execute(
                f"SELECT {column}, payload FROM {table}"  # noqa: S608 - closed table list
            ):
                if not str(identifier).startswith(LEGACY_PREFIXES):
                    continue
                try:
                    payload = json.loads(payload_json) if payload_json else None
                except json.JSONDecodeError:
                    payload = None
                findings.append(
                    {
                        "path": str(path),
                        "table": table,
                        **classify(str(identifier), payload),
                    }
                )
    finally:
        connection.close()
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    findings: list[dict[str, Any]] = []
    for target in args.paths:
        paths = target.rglob("*") if target.is_dir() else (target,)
        for path in paths:
            if not path.is_file():
                continue
            if path.suffix == ".jsonl":
                findings.extend(scan_jsonl(path))
            elif path.suffix in {".sqlite", ".db"}:
                findings.extend(scan_sqlite(path))
    report = {
        "schema_version": 1,
        "read_only": True,
        "finding_count": len(findings),
        "findings": findings,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
