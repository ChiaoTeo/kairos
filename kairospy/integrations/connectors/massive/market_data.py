from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from kairospy.integrations.connectors import ProviderEstimate, ProviderHealth, ProviderResource, SourceArtifact

from .client import MassiveClient
from .reference_pipeline import MassiveReferencePipeline
from .vendor_archive import ArchivedRequest, MassiveVendorArchiveClient, request_fingerprint


@dataclass(frozen=True, slots=True)
class MassiveAggregateBarsRequest:
    symbol: str
    multiplier: int
    timespan: str
    start: date
    end: date
    adjusted: bool
    limit: int = 50000

    def __post_init__(self) -> None:
        if not self.symbol.strip() or self.multiplier <= 0:
            raise ValueError("Massive aggregate bars request requires symbol and positive multiplier")
        if self.timespan not in {"minute", "hour", "day"}:
            raise ValueError("Massive aggregate bars request supports minute, hour or day")
        if self.start > self.end:
            raise ValueError("Massive aggregate bars request requires start <= end")

    @property
    def resource_path(self) -> str:
        return (
            f"/v2/aggs/ticker/{self.symbol.upper()}/range/"
            f"{self.multiplier}/{self.timespan}/{self.start.isoformat()}/{self.end.isoformat()}"
        )

    @property
    def params(self) -> dict[str, object]:
        return {"adjusted": self.adjusted, "sort": "asc", "limit": self.limit}


@dataclass(frozen=True, slots=True)
class MassiveAggregateBarsArtifact:
    artifact: SourceArtifact
    archive: ArchivedRequest


class MassiveAggregateBarsResource(ProviderResource):
    resource_id = "equity_aggregate_bars"

    def __init__(self, root: str | Path, source: MassiveVendorArchiveClient) -> None:
        self.root = Path(root)
        self.source = source

    def fetch(self, request: MassiveAggregateBarsRequest) -> MassiveAggregateBarsArtifact:
        archive = self.source.fetch_pages(request.resource_path, request.params)
        receipt_path = archive.directory / "receipt.json"
        artifact = SourceArtifact(
            provider="massive",
            service="historical_market_data",
            resource=self.resource_id,
            request_fingerprint=archive.fingerprint,
            receipt_path=_relative_to(receipt_path, self.root),
            coverage_hint={
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "boundary": "[start,end]",
            },
            metadata={
                "symbol": request.symbol.upper(),
                "multiplier": request.multiplier,
                "timespan": request.timespan,
                "adjusted": request.adjusted,
            },
        )
        return MassiveAggregateBarsArtifact(artifact=artifact, archive=archive)

    def cached(self, request: MassiveAggregateBarsRequest) -> bool:
        directory = (
            self.root / "source" / "provider=massive"
            / f"resource={_safe_resource(request.resource_path)}"
            / f"request_id={request_fingerprint(request.resource_path, request.params)}"
        )
        receipt = directory / "receipt.json"
        if not receipt.exists():
            return False
        try:
            import json

            value = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return value.get("status") == "complete"

    def iter_results(self, artifact: MassiveAggregateBarsArtifact):
        return self.source.iter_results(artifact.archive)


class MassiveHistoricalMarketDataService:
    service_id = "historical_market_data"
    service_kind = "historical_market_data"

    def __init__(self, root: str | Path, client: MassiveClient, *, source: MassiveVendorArchiveClient | None = None) -> None:
        self.root = Path(root)
        self.client = client
        self.aggregate_bars = MassiveAggregateBarsResource(self.root, source or MassiveVendorArchiveClient(root, client))

    @property
    def source(self):
        return self.aggregate_bars.source

    @source.setter
    def source(self, value) -> None:
        self.aggregate_bars.source = value

    def resources(self) -> Mapping[str, ProviderResource]:
        return {self.aggregate_bars.resource_id: self.aggregate_bars}

    def health(self) -> ProviderHealth:
        return ProviderHealth("massive", "configured", services={self.service_id: "configured"})

    def estimate(self, requests: int, *, instruments: int | None = None, cost_class: str = "entitled-rest") -> ProviderEstimate:
        return ProviderEstimate(requests, cost_class=cost_class, instruments=instruments)

    def fetch_aggregate_bars(self, request: MassiveAggregateBarsRequest) -> MassiveAggregateBarsArtifact:
        return self.aggregate_bars.fetch(request)

    def aggregate_bars_cached(self, request: MassiveAggregateBarsRequest) -> bool:
        return self.aggregate_bars.cached(request)

    def iter_aggregate_bar_results(self, artifact: MassiveAggregateBarsArtifact):
        return self.aggregate_bars.iter_results(artifact)

    def discover_equity_symbols(self) -> tuple[str, ...]:
        discover = getattr(self.source, "discover_symbols", None)
        if discover is not None:
            return tuple(str(item).upper() for item in discover(self.root / "source"))
        records_file = _latest_equity_ticker_records(self.root)
        if records_file is None:
            manifest = MassiveReferencePipeline(self.root, self.client).sync_equity_tickers(include_inactive=False)
            records_file = Path(str(manifest["records_file"]))
        import json

        records = json.loads(records_file.read_text(encoding="utf-8"))
        return tuple(sorted({
            str(item["ticker"]).upper()
            for item in records
            if isinstance(item, dict) and item.get("ticker") and item.get("active", True)
        }))


def _latest_equity_ticker_records(root: Path) -> Path | None:
    versions = sorted((root / "reference" / "provider=massive" / "equity_tickers").glob("version=*/records.json"))
    return versions[-1] if versions else None


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_resource(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value.strip("/"))[:160] or "root"
