from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from pathlib import Path

from trading.research.validation.models import DataCapabilities, ProductProtocol, ValidationLevel
from trading.storage.data_lake import write_json

from .catalog import DataCatalog


def dataset_capabilities(dataset_id: str) -> DataCapabilities:
    profiles = {
        DataCatalog.BTC_SPOT_DAILY.dataset_id: DataCapabilities((dataset_id,), point_in_time_universe=True,
            supported_products=(ProductProtocol.SPOT,), maximum_validation_level=ValidationLevel.L2_SIGNAL),
        DataCatalog.BTC_DVOL_DAILY.dataset_id: DataCapabilities((dataset_id,), maximum_validation_level=ValidationLevel.L2_SIGNAL),
        DataCatalog.BTC_IV_RV_DAILY.dataset_id: DataCapabilities((dataset_id,), maximum_validation_level=ValidationLevel.L2_SIGNAL),
        DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id: DataCapabilities((dataset_id,), point_in_time_universe=True,
            synchronous_quotes=True, top_of_book=True, supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L3_MAPPING),
        DataCatalog.BTC_TERM_SKEW_HOURLY.dataset_id: DataCapabilities((dataset_id,), point_in_time_universe=True,
            supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L2_SIGNAL),
        DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id: DataCapabilities((dataset_id,), point_in_time_universe=True,
            trade_events=True, trade_direction=True, supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L3_MAPPING),
        DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id: DataCapabilities((dataset_id,), point_in_time_universe=True,
            supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L2_SIGNAL),
        DataCatalog.BTC_DERIBIT_OPTION_QUOTES.dataset_id: DataCapabilities((dataset_id,), point_in_time_universe=True,
            synchronous_quotes=True, top_of_book=True, supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L3_MAPPING),
    }
    try: return profiles[dataset_id]
    except KeyError as error: raise KeyError(f"no governed data-capability profile for {dataset_id}") from error


def capabilities_payload(dataset_id: str) -> dict[str, object]:
    return {"capability_schema_version": 1, "dataset_id": dataset_id, **_jsonable(asdict(dataset_capabilities(dataset_id)))}


def materialize_catalog_capabilities(root: str | Path = "data") -> tuple[Path, ...]:
    catalog=DataCatalog(root);written=[]
    for definition in catalog.definitions():
        path=catalog.path(definition.dataset_id)
        if not (path/"manifest.json").exists(): continue
        try: payload=capabilities_payload(definition.dataset_id)
        except KeyError: continue
        target=path/"capabilities.json";write_json(target,payload);written.append(target)
    return tuple(written)


def _jsonable(value):
    if isinstance(value,dict): return {key:_jsonable(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)): return [_jsonable(item) for item in value]
    if isinstance(value,Enum): return value.value
    return value
