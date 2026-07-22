from __future__ import annotations

from dataclasses import replace

from ..contracts import (
    DataProductDefinition,
    DatasetKey,
    DatasetLayer,
    QualityLevel,
    SourceBinding,
)


def _governed(product: DataProductDefinition, description: str) -> DataProductDefinition:
    owner = "workspace-platform" if product.layer.value == "features" else "data-platform"
    return replace(product, description=description, owner=owner)


def _product(
    key: str,
    title: str,
    layer: DatasetLayer,
    dimensions: dict[str, str],
    *,
    primary_time: str = "available_time",
    sources: tuple[SourceBinding, ...] = (),
) -> DataProductDefinition:
    return DataProductDefinition(
        DatasetKey(key),
        title,
        layer,
        dimensions=dimensions,
        primary_time=primary_time,
        sources=sources,
    )


def _capabilities(
    *,
    point_in_time_universe: bool = False,
    synchronous_quotes: bool = False,
    top_of_book: bool = False,
    trade_events: bool = False,
    trade_direction: bool = False,
    products: tuple[str, ...] = (),
    maximum_validation_level: int = 2,
) -> dict[str, object]:
    return {
        "point_in_time_universe": point_in_time_universe,
        "synchronous_quotes": synchronous_quotes,
        "top_of_book": top_of_book,
        "quote_size": False,
        "order_book_depth": False,
        "trade_events": trade_events,
        "trade_direction": trade_direction,
        "queue_reconstructable": False,
        "settlement_price": False,
        "lifecycle_events": False,
        "supported_products": list(products),
        "supported_return_drivers": [],
        "maximum_validation_level": maximum_validation_level,
    }
