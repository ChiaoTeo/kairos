from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Mapping

from kairospy.trading.identity import InstrumentId
from kairospy.reference import MappingTargetType, ProviderId, ProviderSymbolMapping
from kairospy.storage.data_lake import write_json


@dataclass(frozen=True, slots=True)
class MassiveEquityIdentityResult:
    mappings: tuple[ProviderSymbolMapping, ...]
    instruments: tuple[dict[str, object], ...]
    quarantined: tuple[dict[str, object], ...]
    content_sha256: str


class MassiveEquityIdentityResolver:
    """Build stable Massive equity identities from reference rows and ticker events."""

    provider = ProviderId("massive")
    namespace = "stocks"

    def resolve(
        self, reference_rows: Iterable[Mapping[str, object]], ticker_events: Iterable[Mapping[str, object]] = (),
    ) -> MassiveEquityIdentityResult:
        rows = [dict(item) for item in reference_rows]
        events = [dict(item) for item in ticker_events]
        parent: dict[str, str] = {}
        keys_by_ticker: dict[str, list[tuple[datetime, datetime | None, str]]] = {}
        quarantine: list[dict[str, object]] = []

        for row in rows:
            ticker = _ticker(row)
            key = _identity_key(row)
            parent.setdefault(key, key)
            keys_by_ticker.setdefault(ticker, []).append((_start(row), _end(row), key))
            if figi := row.get("provider_composite_figi") or row.get("composite_figi") or row.get("share_class_figi"):
                figi_value = f"figi:{figi}"
                parent.setdefault(figi_value, figi_value)
                _union(parent, key, figi_value)

        for event in events:
            old = str(event.get("old_ticker") or event.get("ticker") or "").upper()
            new = str(event.get("new_ticker") or event.get("new_symbol") or event.get("ticker_to") or "").upper()
            if not old or not new:
                quarantine.append({"reason": "ticker_event_missing_symbol", "event": event})
                continue
            event_at = _date_time(event.get("event_date") or event.get("date") or event.get("effective_date"))
            old_key = _key_for_event(keys_by_ticker, old, event_at, prefer_ending=True)
            new_key = _key_for_event(keys_by_ticker, new, event_at, prefer_ending=False)
            if old_key is None or new_key is None:
                quarantine.append({"reason": "ticker_event_unmapped_symbol", "old_ticker": old, "new_ticker": new, "event": event})
                continue
            parent.setdefault(old_key, old_key); parent.setdefault(new_key, new_key)
            _union(parent, old_key, new_key)

        instrument_by_root: dict[str, InstrumentId] = {}
        mappings: list[ProviderSymbolMapping] = []
        instruments: list[dict[str, object]] = []
        seen_mapping_keys: set[tuple[str, datetime, datetime | None]] = set()
        for row in sorted(rows, key=lambda item: (_start(item), _ticker(item))):
            ticker = _ticker(row)
            root = _find(parent, _identity_key(row))
            instrument = instrument_by_root.get(root)
            if instrument is None:
                instrument = InstrumentId(f"equity:us:massive:{_slug(root)}")
                instrument_by_root[root] = instrument
                instruments.append({
                    "instrument_id": instrument.value,
                    "identity_root": root,
                    "security_type": str(row.get("type") or row.get("security_type") or "CS"),
                    "name": row.get("name"),
                    "currency": row.get("currency_name") or row.get("currency") or "USD",
                    "primary_exchange": row.get("primary_exchange"),
                    "listing_date": row.get("listing_date") or row.get("list_date"),
                    "delisting_date": row.get("delisting_date"),
                    "active": bool(row.get("active", True)),
                })
            start, end = _start(row), _end(row)
            key = (ticker, start, end)
            if key in seen_mapping_keys:
                quarantine.append({"reason": "duplicate_mapping_interval", "ticker": ticker, "row": row})
                continue
            seen_mapping_keys.add(key)
            mappings.append(ProviderSymbolMapping(
                self.provider, self.namespace, ticker, MappingTargetType.INSTRUMENT,
                instrument.value, start, end,
            ))

        payload = {
            "mappings": [_mapping_payload(item) for item in mappings],
            "instruments": instruments,
            "quarantined": quarantine,
        }
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        return MassiveEquityIdentityResult(tuple(mappings), tuple(instruments), tuple(quarantine), digest)

    def save(self, result: MassiveEquityIdentityResult, root: str | Path, *, name: str = "equity_identity") -> dict[str, object]:
        root = Path(root)
        directory = root / "reference" / "provider=massive" / name / f"version={result.content_sha256}"
        write_json(directory / "mappings.json", [_mapping_payload(item) for item in result.mappings])
        write_json(directory / "instruments.json", list(result.instruments))
        write_json(directory / "quarantine.json", list(result.quarantined))
        manifest = {
            "manifest_version": 1,
            "provider": "massive",
            "name": name,
            "mapping_count": len(result.mappings),
            "instrument_count": len(result.instruments),
            "quarantine_count": len(result.quarantined),
            "sha256": result.content_sha256,
        }
        write_json(directory / "manifest.json", manifest)
        if name == "equity_identity":
            from kairospy.data.bootstrap import register_default_products
            from kairospy.data.catalog import DataCatalog
            from kairospy.data.contracts import DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
            from kairospy.data.quality import DatasetQualityService

            register_default_products(root)
            catalog = DataCatalog(root)
            product = catalog.product("reference.identity.equity.us.massive")
            release_id = f"identity_{result.content_sha256[:24]}"
            catalog.register_release(DatasetRelease(
                release_id,
                product.key,
                f"content.{result.content_sha256[:16]}",
                "reference.identity.equity.us.massive.v1",
                "1",
                "massive.equity_identity",
                "1",
                str(directory.relative_to(root)),
                "json",
                result.content_sha256,
                "massive",
                "us-securities",
                ("reference.identity.equity.us.massive@latest-workspace",),
                DatasetStatus.APPROVED_FOR_WORKSPACE,
                QualityLevel.WORKSPACE,
                storage_kind=DatasetStorageKind.REFERENCE,
            ))
            catalog.save()
            assessment = DatasetQualityService(root).assess(release_id)
            manifest["release_id"] = release_id
            manifest["quality_level"] = assessment.level.value
            manifest["quality_passed"] = assessment.passed
            write_json(directory / "manifest.json", manifest)
        return manifest


def _identity_key(row: Mapping[str, object]) -> str:
    for key in ("provider_composite_figi", "composite_figi", "share_class_figi"):
        value = row.get(key)
        if value:
            return f"figi:{value}"
    return f"ticker:{_ticker(row)}:{_start(row).date().isoformat()}"


def _ticker(row: Mapping[str, object]) -> str:
    value = str(row.get("ticker") or "").upper()
    if not value:
        raise ValueError("Massive equity reference row is missing ticker")
    return value


def _start(row: Mapping[str, object]) -> datetime:
    return _date_time(row.get("effective_from") or row.get("listing_date") or row.get("list_date"))


def _end(row: Mapping[str, object]) -> datetime | None:
    value = row.get("effective_to") or row.get("delisting_date")
    return _date_time(value) if value else None


def _date_time(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.combine(datetime.fromisoformat(str(value)).date(), time.min)
    else:
        parsed = datetime(1900, 1, 1)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _find(parent: dict[str, str], key: str) -> str:
    parent.setdefault(key, key)
    while parent[key] != key:
        parent[key] = parent[parent[key]]
        key = parent[key]
    return key


def _union(parent: dict[str, str], left: str, right: str) -> None:
    root_left, root_right = _find(parent, left), _find(parent, right)
    if root_left != root_right:
        parent[max(root_left, root_right)] = min(root_left, root_right)


def _key_for_event(
    keys_by_ticker: dict[str, list[tuple[datetime, datetime | None, str]]],
    ticker: str,
    event_at: datetime,
    *,
    prefer_ending: bool,
) -> str | None:
    intervals = keys_by_ticker.get(ticker, ())
    active = [key for start, end, key in intervals if start <= event_at and (end is None or event_at < end)]
    if active:
        return active[0]
    if prefer_ending:
        ended = [(end, key) for _start_at, end, key in intervals if end is not None and end <= event_at]
        if ended:
            return sorted(ended, key=lambda item: item[0], reverse=True)[0][1]
    future = [(start, key) for start, _end_at, key in intervals if start >= event_at]
    if future:
        return sorted(future, key=lambda item: item[0])[0][1]
    return None


def _slug(value: str) -> str:
    clean = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-")[:80]


def _mapping_payload(mapping: ProviderSymbolMapping) -> dict[str, object]:
    return {
        "provider_id": mapping.provider_id.value,
        "namespace": mapping.namespace,
        "external_id": mapping.external_id,
        "target_type": mapping.target_type.value,
        "target_id": mapping.target_id,
        "effective_from": mapping.effective_from.isoformat(),
        "effective_to": mapping.effective_to.isoformat() if mapping.effective_to else None,
        "publisher_id": mapping.publisher_id,
    }
