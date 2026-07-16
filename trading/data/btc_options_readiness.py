from __future__ import annotations

import math
from pathlib import Path

from .catalog import DataCatalog
from .repository import CanonicalDatasetRepository


def btc_options_readiness(root: str | Path = "data") -> dict[str, object]:
    repository = CanonicalDatasetRepository(root)
    trade_meta = repository.metadata(DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id)
    feature_meta = repository.metadata(DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id)
    rows = repository.load_rows(DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id)
    gates = []
    active_days = trade_meta["coverage"]["observed_window"]["active_days"]
    gates.append(_gate("deribit_active_days", active_days, 730))
    feature_days = feature_meta["coverage"]["coverage"]["observed_periods"]
    gates.append(_gate("term_surface_days", feature_days, 730))
    for horizon in (7, 14, 30, 60, 90):
        ratio = sum(row.get(f"put_skew25_{horizon}d", "") != "" for row in rows)/len(rows)
        gates.append({"name": f"put_skew25_{horizon}d_coverage", "value": ratio, "minimum": .85, "passed": ratio >= .85})
    try:
        quote_meta = repository.metadata(DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id)
        quote_start = quote_meta["coverage"]["coverage"]["start"][:10]
        quote_end = quote_meta["coverage"]["coverage"]["end"][:10]
        quote_days = (date_from(quote_end)-date_from(quote_start)).days
        capability = quote_meta["capabilities"]
    except (FileNotFoundError, KeyError):
        quote_days = 0; capability = {}
    cross_validation = _gate("binance_quote_days_cross_validation", quote_days, 90)
    strategy = _gate("binance_quote_days_strategy", quote_days, 252)
    execution_gates = [
        {"name": "synchronous_quotes", "value": bool(capability.get("synchronous_quotes")), "minimum": True,
         "passed": bool(capability.get("synchronous_quotes"))},
        {"name": "quote_size", "value": bool(capability.get("quote_size")), "minimum": True,
         "passed": bool(capability.get("quote_size"))},
        {"name": "settlement_price", "value": bool(capability.get("settlement_price")), "minimum": True,
         "passed": bool(capability.get("settlement_price"))},
        {"name": "option_lifecycle_events", "value": bool(capability.get("lifecycle_events")), "minimum": True,
         "passed": bool(capability.get("lifecycle_events"))},
    ]
    return {"dataset_family": "btc_options", "signal_research_ready": all(gate["passed"] for gate in gates),
            "executable_strategy_ready": strategy["passed"] and all(gate["passed"] for gate in execution_gates),
            "gates": gates+[cross_validation,strategy]+execution_gates,
            "blocked_execution_capabilities": [gate["name"] for gate in execution_gates if not gate["passed"]]}


def _gate(name, value, minimum):
    return {"name": name, "value": value, "minimum": minimum, "passed": value >= minimum}


def date_from(value):
    from datetime import date
    return date.fromisoformat(value)
