from __future__ import annotations

from pathlib import Path

from trading.data.products import (
    BTC_DERIBIT_OPTION_TRADES, BTC_DERIBIT_TERM_SKEW_DAILY, BTC_OPTION_QUOTES_HOURLY,
)
from trading.data.client import ResearchDataClient


def btc_options_readiness(root: str | Path = "data") -> dict[str, object]:
    """Study-family gate; platform release quality is evaluated separately."""
    repository = ResearchDataClient(root)
    trade_meta = repository.metadata(BTC_DERIBIT_OPTION_TRADES.product)
    feature_meta = repository.metadata(BTC_DERIBIT_TERM_SKEW_DAILY.product)
    rows = repository.load_rows(BTC_DERIBIT_TERM_SKEW_DAILY.product)
    gates = []
    active_days = trade_meta["coverage"]["observed_window"]["active_days"]
    gates.append(_gate("deribit_active_days", active_days, 730))
    feature_days = feature_meta["coverage"]["coverage"]["observed_periods"]
    gates.append(_gate("term_surface_days", feature_days, 730))
    for horizon in (7, 14, 30, 60, 90):
        ratio = sum(row.get(f"put_skew25_{horizon}d", "") != "" for row in rows) / len(rows)
        gates.append({"name": f"put_skew25_{horizon}d_coverage", "value": ratio,
                      "minimum": .85, "passed": ratio >= .85})
    try:
        quote_meta = repository.metadata(BTC_OPTION_QUOTES_HOURLY.product)
        quote_start = quote_meta["coverage"]["coverage"]["start"][:10]
        quote_end = quote_meta["coverage"]["coverage"]["end"][:10]
        quote_days = (_date(quote_end) - _date(quote_start)).days
        capability = quote_meta["capabilities"]
    except (FileNotFoundError, KeyError):
        quote_days, capability = 0, {}
    cross_validation = _gate("binance_quote_days_cross_validation", quote_days, 90)
    strategy = _gate("binance_quote_days_strategy", quote_days, 252)
    execution_gates = [
        {"name": name, "value": bool(capability.get(field)), "minimum": True,
         "passed": bool(capability.get(field))}
        for name, field in (
            ("synchronous_quotes", "synchronous_quotes"), ("quote_size", "quote_size"),
            ("settlement_price", "settlement_price"), ("option_lifecycle_events", "lifecycle_events"),
        )
    ]
    return {
        "dataset_family": "btc_options", "signal_research_ready": all(gate["passed"] for gate in gates),
        "executable_strategy_ready": strategy["passed"] and all(gate["passed"] for gate in execution_gates),
        "gates": gates + [cross_validation, strategy] + execution_gates,
        "blocked_execution_capabilities": [gate["name"] for gate in execution_gates if not gate["passed"]],
    }


def _gate(name, value, minimum):
    return {"name": name, "value": value, "minimum": minimum, "passed": value >= minimum}


def _date(value):
    from datetime import date
    return date.fromisoformat(value)
