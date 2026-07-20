from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from kairospy.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DatasetRelease, DatasetStatus, DatasetStorageKind,
    QualityLevel, SourceBinding,
)
from kairospy.market_data.repository import ParquetMarketEventRepository

from .client import MassiveClient
from .client import MassiveError
from .decoder import decode_bars, decode_quotes, decode_trades
from .reference import MassiveReferenceImporter
from .vendor_archive import ArchivedRequest, MassiveVendorArchiveClient


class MassiveOptionDataPipeline:
    def __init__(self, root: str | Path, client: MassiveClient, *, catalog_path: str | Path | None = None,
                 mapping_path: str | Path | None = None, now=lambda: datetime.now(timezone.utc)) -> None:
        self.root, self.client, self.now = Path(root), client, now
        self.source = MassiveVendorArchiveClient(root, client, now=now)
        reference_path = catalog_path or mapping_path or self.root / "reference" / "catalog.json"
        self.catalog_repository = ReferenceCatalogRepository(reference_path)
        self.events = ParquetMarketEventRepository(self.root / "canonical" / "market")

    def prepare_options(self, *, dataset_id: str, underlying: str, option_tickers: tuple[str, ...], start: datetime, end: datetime,
                        underlying_reference_ticker: str | None = None, register: bool = True) -> dict[str, object]:
        if start.tzinfo is None or end.tzinfo is None or not start < end:
            raise ValueError("Massive option pipeline requires timezone-aware [start,end)")
        if not option_tickers:
            raise ValueError("Massive option pipeline requires explicit option tickers")
        catalog = self.catalog_repository.load() if self.catalog_repository.path.exists() else ReferenceCatalog()
        importer = MassiveReferenceImporter(catalog)

        reference_ticker = underlying_reference_ticker or _reference_ticker(underlying)
        underlying_archive = self.source.fetch_pages(f"/v3/reference/tickers/{reference_ticker}", {"date": start.date().isoformat()})
        underlying_rows = tuple(self.source.iter_results(underlying_archive))
        if len(underlying_rows) != 1:
            raise RuntimeError(f"Massive underlying lookup returned {len(underlying_rows)} rows for {underlying}")
        importer.import_underlyings(underlying_rows, as_of=start)

        underlying_namespace = "indices" if str(underlying_rows[0].get("market", "")).lower() == "indices" or str(underlying_rows[0].get("ticker", "")).startswith("I:") else "stocks"
        aggregates_archive = None
        aggregate_error = None
        aggregate_rows: tuple[dict[str, object], ...] = ()
        try:
            aggregates_archive = self.source.fetch_pages(
                f"/v2/aggs/ticker/{reference_ticker}/range/1/minute/{start.date().isoformat()}/{end.date().isoformat()}",
                {"adjusted": True, "sort": "asc", "limit": 50000},
            )
            aggregate_rows = tuple(self.source.iter_results(aggregates_archive))
        except MassiveError as error:
            if underlying_namespace != "indices":
                raise
            aggregate_error = str(error)
        underlying_events = tuple(event for event in decode_bars(
            aggregate_rows, catalog, ticker=reference_ticker, source_namespace=underlying_namespace,
            ingested_at=_ingested_at(aggregates_archive) if aggregates_archive is not None else self.now(), interval_seconds=60,
        ) if start <= event.available_time < end)

        contract_archives, selected_rows = [], []
        for ticker in option_tickers:
            archive = self.source.fetch_pages("/v3/reference/options/contracts", {
                "ticker": ticker, "as_of": start.date().isoformat(), "limit": 10,
            })
            contract_archives.append(archive)
            selected_rows.extend(row for row in self.source.iter_results(archive) if str(row.get("ticker")) == ticker)
        selected = tuple(selected_rows)
        missing = sorted(set(option_tickers) - {str(row.get("ticker")) for row in selected})
        if missing:
            raise RuntimeError(f"Massive option contracts missing requested tickers: {', '.join(missing)}")
        importer.import_option_contracts(selected, as_of=start)
        self.catalog_repository.save(catalog)

        exchanges_archive = self.source.fetch_pages("/v3/reference/exchanges", {"asset_class": "options"})
        conditions_archive = self.source.fetch_pages("/v3/reference/conditions", {"asset_class": "options"})
        exchange_rows = tuple(self.source.iter_results(exchanges_archive)); condition_rows = tuple(self.source.iter_results(conditions_archive))
        exchange_codes = {str(row["id"]) for row in exchange_rows if row.get("id") is not None}
        condition_codes = {str(row["id"]) for row in condition_rows if row.get("id") is not None}

        sources = [underlying_archive, *contract_archives, exchanges_archive, conditions_archive]
        if aggregates_archive is not None:
            sources.insert(1, aggregates_archive)
        events, source_order, delivered_event_records, quarantined = list(underlying_events), len(aggregate_rows), len(aggregate_rows), []
        for ticker in option_tickers:
            params = {"timestamp.gte": start.isoformat(), "timestamp.lt": end.isoformat(), "limit": 50000, "sort": "timestamp", "order": "asc"}
            quote_archive = self.source.fetch_pages(f"/v3/quotes/{ticker}", params)
            trade_archive = self.source.fetch_pages(f"/v3/trades/{ticker}", params)
            quote_rows = tuple(self.source.iter_results(quote_archive)); trade_rows = tuple(self.source.iter_results(trade_archive))
            delivered_event_records += len(quote_rows) + len(trade_rows)
            quotes = self._decode_rows(decode_quotes, catalog, quote_rows, source_order, quarantined, _ingested_at(quote_archive), ticker)
            source_order += len(quote_rows)
            trades = self._decode_rows(decode_trades, catalog, trade_rows, source_order, quarantined, _ingested_at(trade_archive), ticker)
            source_order += len(trade_rows)
            events.extend(quotes); events.extend(trades); sources.extend((quote_archive, trade_archive))
        if quarantined:
            directory = self.root / "quarantine" / "provider=massive" / f"dataset={dataset_id}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "unmapped-or-invalid.jsonl"
            path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n" for item in quarantined), encoding="utf-8")
            raise RuntimeError(f"Massive dataset blocked: {len(quarantined)} records quarantined at {path}")
        lineage = {
            "lineage_version": 2, "dataset_id": dataset_id,
            "producer": {"name": "kairospy.connectors.massive.pipeline", "version": 1},
            "source": {"provider": "massive", "api_base": "https://api.massiveprivateserver.site"},
            "request_window": {"start": start.isoformat(), "end": end.isoformat(), "boundary": "[start,end)"},
            "source_receipts": [str(item.directory.relative_to(self.root) / "receipt.json") for item in sources],
            "visibility": {"clock": "provider_published", "quotes": "sip_timestamp", "trades": "sip_timestamp", "aggregates": "period_end"},
            "underlying_reference": {
                "ticker": reference_ticker,
                "official_history_available": bool(underlying_events),
                "fallback": "put_call_parity_synthetic_forward" if not underlying_events else None,
                "aggregate_error": aggregate_error,
            },
        }
        manifest = self.events.write_batch(dataset_id, events, lineage=lineage, reconciliation={
            "delivered_event_records": delivered_event_records, "decoded_event_records": len(events),
            "canonical_event_records": len(events), "filtered_outside_request_window": delivered_event_records - len(events),
            "quarantined_records": 0, "known_exchange_codes": len(exchange_codes), "known_condition_codes": len(condition_codes),
        }, known_exchange_codes=exchange_codes, known_condition_codes=condition_codes)
        if register:
            logical_key = DatasetKey(f"market.events.options.us.{underlying.lower()}")
            product = DataProductDefinition(
                logical_key, f"{underlying.upper()} option market events", DatasetLayer.CANONICAL,
                dimensions={"asset_class": "option", "region": "us", "underlying": underlying.upper(),
                            "frequency": "event", "venue_scope": "opra"},
                sources=(SourceBinding("massive", "opra", 100, QualityLevel.BACKTEST, ("rest",)),),
            )
            release = DatasetRelease(
                dataset_id, logical_key, str(manifest["generated_at"]), "market.event_envelope.v1", "1",
                "massive.option_events", "2", f"canonical/market/dataset={dataset_id}", "parquet",
                str(manifest["dataset_sha256"]), "massive", "opra",
                (f"{logical_key}@latest-validated",), DatasetStatus.APPROVED_FOR_BACKTEST,
                QualityLevel.BACKTEST, str(manifest["generated_at"]),
                DatasetStorageKind.MARKET_EVENTS, "1",
            )
            catalog = DataCatalog(self.root); catalog.register_product(product, enrich=True)
            catalog.register_release(release); catalog.save()
        return manifest

    def _decode_rows(self, decoder, catalog: ReferenceCatalog, rows, source_order: int, quarantined: list[dict[str, object]], ingested_at: datetime, ticker: str):
        events = []
        for offset, row in enumerate(rows):
            try:
                events.extend(decoder((row,), catalog, ingested_at=ingested_at, source_order_start=source_order + offset, ticker=ticker))
            except (LookupError, ValueError) as error:
                quarantined.append({"row": row, "error_type": type(error).__name__, "error": str(error)})
        return tuple(events)


def _ingested_at(archive: ArchivedRequest) -> datetime:
    value = datetime.fromisoformat(str(archive.receipt["completed_at"]))
    if value.tzinfo is None:
        raise ValueError("Massive receipt completed_at must be timezone-aware")
    return value


def _reference_ticker(underlying: str) -> str:
    return f"I:{underlying}" if underlying.upper() in {"SPX", "VIX", "NDX", "RUT", "DJI"} and not underlying.startswith("I:") else underlying
