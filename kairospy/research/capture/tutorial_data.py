from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kairospy.data.contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetStorageKind,
    QualityLevel, SourceBinding,
)
from kairospy.data.storage.store import DatasetStore
from kairospy.data.storage.writer import DatasetWriter
from kairospy.identity import InstrumentId
from kairospy.market.types import Bar


SMA_TUTORIAL_RELEASE_ID = "fixture:sma-bars-v1"

SMA_TUTORIAL_DATASET = DataProductContract(
    DataProductDefinition(
        DatasetKey("market.ohlcv.crypto.tutorial.btc-usdt.1h"),
        "Deterministic BTC/USDT hourly tutorial bars",
        DatasetLayer.CANONICAL,
        description="Synthetic point-in-time OHLCV bars for the first workspace tutorial.",
        dimensions={"asset_class": "crypto", "venue": "tutorial", "instrument": "BTC-USDT", "frequency": "1h"},
        primary_time="available_time",
        sources=(SourceBinding("synthetic-fixture", "tutorial", 100, QualityLevel.BACKTEST, ("bundled",)),),
        owner="workspace-platform",
    ),
    "canonical/tutorial/market/ohlcv/instrument=BTC-USDT/interval=1h",
    "market.ohlcv.v1",
    capabilities={"point_in_time_universe": True, "supported_products": ["spot"], "maximum_validation_level": 2},
    storage_kind=DatasetStorageKind.TABULAR,
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.BACKTEST,
)


def tutorial_sma_bars() -> tuple[Bar, ...]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
    values = []
    for index in range(90):
        close = Decimal("100") + Decimal(index % 30) - Decimal((index // 30) * 8)
        values.append(Bar(
            instrument, start + timedelta(hours=index), start + timedelta(hours=index + 1),
            close - Decimal("0.5"), close + Decimal("1"), close - Decimal("1"), close,
            Decimal("10") + index,
        ))
    return tuple(values)


def ensure_sma_tutorial_dataset(root: str | Path):
    """Ensure the bundled fixture exists in the simplified DatasetStore."""
    store = DatasetStore(root)
    dataset_id = str(SMA_TUTORIAL_DATASET.key)
    if list(store.data_path(dataset_id).rglob("*.parquet")):
        return dataset_id
    rows = [_bar_row(bar) for bar in tutorial_sma_bars()]
    store.ensure_dataset(
        dataset_id,
        metadata={
            "title": SMA_TUTORIAL_DATASET.product.title,
            "schema": SMA_TUTORIAL_DATASET.schema_id,
            "source": "synthetic-fixture",
        },
    )
    DatasetWriter(store).upsert(
        dataset_id,
        rows,
        key=("instrument_id", "period_start"),
        partition_by=("event_day",),
        time_field="period_start",
    )
    return dataset_id


def _bar_row(bar: Bar) -> dict[str, object]:
    return {
        "instrument_id": bar.instrument_id.value,
        "period_start": bar.start,
        "period_end": bar.end,
        "event_time": bar.end,
        "available_time": bar.end,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
