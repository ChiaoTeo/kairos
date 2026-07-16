from __future__ import annotations

import json
from pathlib import Path

from trading.domain.instrument import InstrumentDefinition
from trading.domain.identity import AssetId, InstrumentId
from trading.domain.product import (
    CryptoOptionSpec, CryptoSpotSpec, EquitySpec, FutureSpec, IndexSpec,
    ListedOptionSpec, PerpetualSpec, ProductType, TokenizedEquitySpec,
)
from trading.storage.codec import from_primitive, to_primitive

from .service import InstrumentCatalog


class CatalogRepository:
    def __init__(self, path: str | Path = "data/catalog/instruments.json") -> None:
        self.path = Path(path)

    def save(self, catalog: InstrumentCatalog) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {"schema_version": 1, "definitions": to_primitive(catalog.definitions())}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    def load(self) -> InstrumentCatalog:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1:
            raise ValueError("unsupported catalog schema version")
        catalog = InstrumentCatalog()
        for item in value["definitions"]:
            catalog.add(definition_from_primitive(item))
        return catalog


def definition_from_primitive(item) -> InstrumentDefinition:
    product_type = ProductType(item["product_type"])
    spec_type = {
        ProductType.INDEX: IndexSpec,
        ProductType.EQUITY: EquitySpec,
        ProductType.ETF: EquitySpec,
        ProductType.LISTED_OPTION: ListedOptionSpec,
        ProductType.CRYPTO_SPOT: CryptoSpotSpec,
        ProductType.FUTURE: FutureSpec,
        ProductType.PERPETUAL: PerpetualSpec,
        ProductType.CRYPTO_OPTION: CryptoOptionSpec,
        ProductType.TOKENIZED_EQUITY: TokenizedEquitySpec,
    }[product_type]
    return from_primitive({**item, "product_spec": from_primitive(item["product_spec"], spec_type)}, InstrumentDefinition)
