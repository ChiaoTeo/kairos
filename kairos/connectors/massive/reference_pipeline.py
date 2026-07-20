from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from kairos.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairos.storage.codec import to_primitive
from kairos.storage.data_lake import write_json

from .client import MassiveClient
from .corporate_actions import MassiveCorporateActionDecoder
from .reference_store import MassiveReferenceStore
from .vendor_archive import MassiveVendorArchiveClient


class MassiveReferencePipeline:
    def __init__(self, root: str | Path, client: MassiveClient, *, mapping_path: str | Path | None = None) -> None:
        self.root = Path(root)
        self.source = MassiveVendorArchiveClient(root, client)
        self.store = MassiveReferenceStore(self.root / "reference" / "provider=massive")
        reference_path = Path(mapping_path) if mapping_path is not None else self.root / "reference" / "catalog.json"
        self.mappings = ReferenceCatalogRepository(reference_path).load() if reference_path.exists() else ReferenceCatalog()

    def sync_code_tables(self) -> tuple[dict[str, object], ...]:
        requests = (
            ("exchanges", "/v3/reference/exchanges", {"asset_class": "options"}),
            ("conditions", "/v3/reference/conditions", {"asset_class": "options"}),
            ("market_holidays", "/v1/marketstatus/upcoming", {}),
        )
        manifests = []
        for name, resource, params in requests:
            archived = self.source.fetch_pages(resource, params)
            manifests.append(self.store.save(name, self.source.iter_results(archived), source_receipt=str(archived.directory / "receipt.json")))
        return tuple(manifests)

    def sync_equity_tickers(self, *, include_inactive: bool = True, security_type: str = "CS") -> dict[str, object]:
        active_states = (True, False) if include_inactive else (True,)
        rows: list[dict[str, object]] = []
        receipts: list[str] = []
        state_manifests: list[dict[str, object]] = []
        for active in active_states:
            archived = self.source.fetch_pages(
                "/v3/reference/tickers",
                {"market": "stocks", "type": security_type, "active": active, "limit": 1000, "sort": "ticker", "order": "asc"},
            )
            chunk = [_normalize_equity_ticker_reference_row(item, active=active) for item in self.source.iter_results(archived)]
            receipt = str(archived.directory / "receipt.json")
            rows.extend(chunk)
            receipts.append(receipt)
            state_manifests.append({"active": active, "records": len(chunk), "source_receipt": receipt})
        manifest = self.store.save("equity_tickers", rows, source_receipt=receipts)
        directory = self.store.root / "equity_tickers" / f"version={manifest['sha256']}"
        return {
            **manifest,
            "directory": str(directory),
            "records_file": str(directory / "records.json"),
            "include_inactive": include_inactive,
            "security_type": security_type,
            "active_states": state_manifests,
        }

    def sync_corporate_actions(self, ticker: str, start: datetime, end: datetime) -> dict[str, object]:
        if start.tzinfo is None or end.tzinfo is None or not start < end:
            raise ValueError("corporate action sync requires timezone-aware [start,end)")
        decoder = MassiveCorporateActionDecoder(self.mappings)
        split_archive = self.source.fetch_pages("/v3/reference/splits", {"ticker": ticker, "execution_date.gte": start.date(), "execution_date.lt": end.date(), "limit": 1000})
        dividend_archive = self.source.fetch_pages("/v3/reference/dividends", {"ticker": ticker, "ex_dividend_date.gte": start.date(), "ex_dividend_date.lt": end.date(), "limit": 1000})
        event_archive = self.source.fetch_pages(f"/vX/reference/tickers/{ticker}/events", {"types": "ticker_change"})
        events = (*decoder.splits(self.source.iter_results(split_archive)), *decoder.dividends(self.source.iter_results(dividend_archive)),
                  *decoder.ticker_events(self.source.iter_results(event_archive)))
        primitive = to_primitive(events)
        digest = sha256(json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        directory = self.root / "reference" / "provider=massive" / "corporate_actions" / f"ticker={ticker}" / f"version={digest}"
        write_json(directory / "events.json", primitive)
        manifest = {"manifest_version": 1, "provider": "massive", "ticker": ticker, "event_count": len(events), "sha256": digest,
                    "boundary": "[start,end)", "start": start.isoformat(), "end": end.isoformat(),
                    "source_receipts": [str(item.directory / "receipt.json") for item in (split_archive, dividend_archive, event_archive)]}
        write_json(directory / "manifest.json", manifest)
        return manifest


def _normalize_equity_ticker_reference_row(row: Mapping[str, object], *, active: bool) -> dict[str, object]:
    value = dict(row)
    if value.get("ticker"):
        value["ticker"] = str(value["ticker"]).upper()
    value["provider"] = "massive"
    value["security_type"] = value.get("security_type") or value.get("type")
    value["active"] = bool(value.get("active", active))
    if not value.get("effective_from"):
        effective_from = value.get("listing_date") or value.get("list_date")
        if effective_from:
            value["effective_from"] = effective_from
    if not value.get("effective_to"):
        effective_to = value.get("delisting_date") or value.get("delisted_utc")
        if effective_to:
            value["effective_to"] = str(effective_to)[:10]
    return value
