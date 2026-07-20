from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
from uuid import uuid4

from kairos.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairos.data.catalog import DataCatalog
from kairos.data.contracts import DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairos.storage.data_lake import write_json

from .client import MassiveClient
from .equity_daily_ohlcv import MassiveEquityDailyOhlcvPipeline
from .pipeline import MassiveOptionDataPipeline


@dataclass(frozen=True, slots=True)
class MassiveOptionProductConfig:
    logical_key: str
    underlying: str
    option_tickers: tuple[str, ...]
    underlying_reference_ticker: str | None = None

    def __post_init__(self) -> None:
        if not self.logical_key or not self.underlying or not self.option_tickers:
            raise ValueError("Massive option connector requires product, underlying and explicit option tickers")
        if any(not ticker.startswith("O:") for ticker in self.option_tickers):
            raise ValueError("Massive option tickers must use the O: namespace")


@dataclass(frozen=True, slots=True)
class MassiveEquityDailyOhlcvProductConfig:
    logical_key: str
    ticker: str
    view: str = "vendor_adjusted"

    def __post_init__(self) -> None:
        if not self.logical_key or not self.ticker:
            raise ValueError("Massive equity connector requires product and ticker")
        if self.view not in {"raw", "vendor_adjusted"}:
            raise ValueError("Massive equity view must be 'raw' or 'vendor_adjusted'")


class MassiveEquityDailyOhlcvDatasetConnector:
    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, config: MassiveEquityDailyOhlcvProductConfig) -> None:
        self.root, self.config = Path(root), config
        self.pipeline = MassiveEquityDailyOhlcvPipeline(root, client)

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days, cost_class="entitled-rest-bounded-ticker")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Massive equity connector received an unsupported acquisition request")
        if not request.missing:
            raise ValueError("Massive equity connector requires a non-empty acquisition window")
        start = min(item.start for item in request.missing).date()
        end = max(item.end for item in request.missing).date()
        staging_id = f"staging_{uuid4().hex}"
        manifest = self.pipeline.prepare(
            staging_id, self.config.ticker, start, end, view=self.config.view,
        )
        final_id = f"ds_{str(manifest['content_sha256'])[:24]}"
        staging = (
            self.root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d"
            / f"view={self.config.view}" / f"dataset={staging_id}"
        )
        final = staging.with_name(f"dataset={final_id}")
        if final.exists():
            existing = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("content_sha256") != manifest["content_sha256"]:
                raise RuntimeError("content-addressed Massive equity release collision")
            shutil.rmtree(staging)
        else:
            staging.replace(final)
            for name in ("manifest", "lineage", "coverage", "quality", "schema"):
                path = final / f"{name}.json"
                value = json.loads(path.read_text(encoding="utf-8"))
                _replace_dataset_id(value, staging_id, final_id)
                write_json(path, value)
        catalog = DataCatalog(self.root)
        product = catalog.product(request.logical_key)
        published_at = str(manifest.get("generated_at") or datetime.now().astimezone().isoformat())
        release = DatasetRelease(
            final_id, product.key, published_at, "market.ohlcv.equity.us.1d.v1", "1",
            "massive.equity_daily_ohlcv", "1", str(final.relative_to(self.root)), "parquet",
            str(manifest["content_sha256"]), "massive", "us-securities",
            (f"{product.key}@latest-study",), DatasetStatus.APPROVED_FOR_STUDY,
            QualityLevel.STUDY, published_at, DatasetStorageKind.TABULAR, "1",
        )
        catalog.register_release(release); catalog.save()
        from kairos.data.release_metadata import ensure_release_metadata
        ensure_release_metadata(self.root, release.release_id)
        return release


class MassiveOptionEventsDatasetConnector:
    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, config: MassiveOptionProductConfig, *,
                 catalog_path: str | Path | None = None, mapping_path: str | Path | None = None) -> None:
        self.root, self.config = Path(root), config
        self.pipeline = MassiveOptionDataPipeline(root, client, catalog_path=catalog_path, mapping_path=mapping_path)

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days * len(self.config.option_tickers) * 3 + 6, cost_class="entitled")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Massive connector received an unsupported acquisition request")
        if not request.missing:
            raise ValueError("Massive connector requires a non-empty acquisition window")
        start = min(item.start for item in request.missing)
        end = max(item.end for item in request.missing)
        if request.base_release_id is not None:
            coverage = DataCatalog(self.root).path(request.base_release_id) / "coverage.json"
            value = json.loads(coverage.read_text(encoding="utf-8"))
            window = value.get("requested_window") or value.get("observed_window") or {}
            previous_start = window.get("start") or window.get("minimum_event_time")
            if previous_start:
                start = min(start, datetime.fromisoformat(str(previous_start).replace("Z", "+00:00")))
        staging_id = f"staging_{uuid4().hex}"
        manifest = self.pipeline.prepare_options(
            dataset_id=staging_id, underlying=self.config.underlying,
            underlying_reference_ticker=self.config.underlying_reference_ticker,
            option_tickers=self.config.option_tickers, start=start, end=end, register=False,
        )
        final_id = f"ds_{str(manifest['dataset_sha256'])[:24]}"
        staging = self.root / "canonical" / "market" / f"dataset={staging_id}"
        final = self.root / "canonical" / "market" / f"dataset={final_id}"
        if final.exists():
            existing = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("dataset_sha256") != manifest["dataset_sha256"]:
                raise RuntimeError("content-addressed Massive release collision")
            shutil.rmtree(staging)
        else:
            staging.replace(final)
            for name in ("manifest", "lineage", "coverage", "quality", "schema"):
                path = final / f"{name}.json"
                if not path.exists():
                    continue
                value = json.loads(path.read_text(encoding="utf-8"))
                _replace_dataset_id(value, staging_id, final_id)
                write_json(path, value)
        catalog = DataCatalog(self.root)
        product = catalog.product(request.logical_key)
        release = DatasetRelease(
            final_id, product.key, str(manifest["generated_at"]), "market.event_envelope.v1", "1",
            "massive.option_events", "2", f"canonical/market/dataset={final_id}", "parquet",
            str(manifest["dataset_sha256"]), "massive", "opra", (f"{product.key}@latest-validated",),
            DatasetStatus.APPROVED_FOR_BACKTEST, QualityLevel.BACKTEST, str(manifest["generated_at"]),
            DatasetStorageKind.MARKET_EVENTS, "1",
        )
        catalog.register_release(release); catalog.save()
        from kairos.data.release_metadata import ensure_release_metadata
        ensure_release_metadata(self.root, release.release_id)
        return release


def _replace_dataset_id(value, previous: str, current: str) -> None:
    if isinstance(value, dict):
        for key, item in tuple(value.items()):
            if key == "dataset_id" and item == previous:
                value[key] = current
            else:
                _replace_dataset_id(item, previous, current)
    elif isinstance(value, list):
        for item in value:
            _replace_dataset_id(item, previous, current)
