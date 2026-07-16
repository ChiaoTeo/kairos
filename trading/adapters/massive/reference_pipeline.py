from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from trading.catalog.external import ExternalMappingRepository
from trading.storage.codec import to_primitive
from trading.storage.data_lake import write_json

from .client import MassiveClient
from .corporate_actions import MassiveCorporateActionDecoder
from .reference_store import MassiveReferenceStore
from .source import MassiveSourceArchive


class MassiveReferencePipeline:
    def __init__(self, root: str | Path, client: MassiveClient, *, mapping_path: str | Path | None = None) -> None:
        self.root = Path(root)
        self.source = MassiveSourceArchive(root, client)
        self.store = MassiveReferenceStore(self.root / "reference" / "provider=massive")
        self.mappings = ExternalMappingRepository(mapping_path or self.root / "reference" / "external_mappings.json")

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
