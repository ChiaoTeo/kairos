from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DatasetDefinition:
    dataset_id: str
    relative_path: str
    schema_id: str
    layer: str


class DataCatalog:
    BTC_SPOT_DAILY = DatasetDefinition(
        "market.ohlcv.crypto.binance.btc-usdt.1d",
        "canonical/market/ohlcv/asset_class=crypto/venue=binance/instrument=BTC-USDT/interval=1d",
        "market.ohlcv.v1", "canonical",
    )
    BTC_DVOL_DAILY = DatasetDefinition(
        "analytics.vendor_volatility_index.deribit.btc-dvol.1d",
        "canonical/analytics/vendor_volatility_indices/provider=deribit/underlying=BTC/index=DVOL/interval=1d",
        "analytics.vendor_volatility_index.v1", "canonical",
    )
    BTC_IV_RV_DAILY = DatasetDefinition(
        "features.volatility.btc.iv-rv.1d.v1",
        "features/volatility/underlying=BTC/frequency=1d/feature_set=iv_rv_v1",
        "features.volatility.iv_rv_daily.v1", "features",
    )
    BTC_OPTION_QUOTES_HOURLY = DatasetDefinition(
        "derivatives.option_quotes.crypto.binance.btc-usdt.1h",
        "canonical/derivatives/option_quotes/asset_class=crypto/venue=binance/underlying=BTC-USDT/interval=1h",
        "derivatives.option_quote_summary.v1", "canonical",
    )
    BTC_TERM_SKEW_HOURLY = DatasetDefinition(
        "features.volatility_surface.btc.term-skew.1h.v1",
        "features/volatility_surface/underlying=BTC/frequency=1h/feature_set=term_skew_v1",
        "features.volatility_surface.term_skew.v1", "features",
    )
    BTC_DERIBIT_OPTION_TRADES = DatasetDefinition(
        "derivatives.option_trades.crypto.deribit.btc.v1",
        "canonical/derivatives/option_trades/asset_class=crypto/venue=deribit/underlying=BTC",
        "derivatives.option_trade.v1", "canonical",
    )
    BTC_DERIBIT_TERM_SKEW_DAILY = DatasetDefinition(
        "features.volatility_surface.btc.deribit-trade-term-skew.1d.v1",
        "features/volatility_surface/underlying=BTC/frequency=1d/feature_set=deribit_trade_term_skew_v1",
        "features.volatility_surface.trade_term_skew.v1", "features",
    )
    BTC_DERIBIT_OPTION_QUOTES = DatasetDefinition(
        "derivatives.option_quotes.crypto.deribit.btc.snapshots.v1",
        "canonical/derivatives/option_quotes/asset_class=crypto/venue=deribit/underlying=BTC",
        "derivatives.option_chain_summary.v1", "canonical",
    )

    def __init__(self, root: str | Path = "data", registry_path: str | Path | None = None) -> None:
        self.root = Path(root)
        self._definitions = {item.dataset_id: item for item in (
            self.BTC_SPOT_DAILY, self.BTC_DVOL_DAILY, self.BTC_IV_RV_DAILY,
            self.BTC_OPTION_QUOTES_HOURLY, self.BTC_TERM_SKEW_HOURLY,
            self.BTC_DERIBIT_OPTION_TRADES, self.BTC_DERIBIT_TERM_SKEW_DAILY,
            self.BTC_DERIBIT_OPTION_QUOTES,
        )}
        path = Path(registry_path) if registry_path is not None else self.root / "catalog" / "datasets.json"
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1:
                raise ValueError("unsupported dataset registry schema version")
            for item in value.get("datasets", []):
                self.register(DatasetDefinition(**item))

    def register(self, definition: DatasetDefinition) -> None:
        previous = self._definitions.get(definition.dataset_id)
        if previous is not None and previous != definition:
            raise ValueError(f"conflicting dataset definition: {definition.dataset_id}")
        self._definitions[definition.dataset_id] = definition

    def get(self, dataset_id: str) -> DatasetDefinition:
        try:
            return self._definitions[dataset_id]
        except KeyError as error:
            raise KeyError(f"unknown dataset {dataset_id!r}; available: {', '.join(sorted(self._definitions))}") from error

    def path(self, dataset_id: str) -> Path:
        return self.root / self.get(dataset_id).relative_path

    def definitions(self) -> tuple[DatasetDefinition, ...]:
        return tuple(self._definitions.values())
