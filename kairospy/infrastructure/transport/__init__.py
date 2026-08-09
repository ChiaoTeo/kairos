"""Python adapters for versioned Kairos process-boundary transports.

Exports are resolved lazily so a contract client can be imported without
eagerly importing the strategy application and recreating an initialization
cycle.
"""

from importlib import import_module

_EXPORTS = {
    "DecimalValue": (".market", "DecimalValue"),
    "EventStreamGap": (".market", "EventStreamGap"),
    "BarView": (".market", "BarView"),
    "GreeksView": (".market", "GreeksView"),
    "MarketDataView": (".market", "MarketDataView"),
    "MmapMarketSnapshotReader": (".market", "MmapMarketSnapshotReader"),
    "QuoteView": (".market", "QuoteView"),
    "TradeView": (".market", "TradeView"),
    "UnixMarketEventStream": (".market", "UnixMarketEventStream"),
    "ExecutionIntentCommandPort": (".commands", "ExecutionIntentCommandPort"),
    "MarketUnixCommandPort": (".commands", "MarketUnixCommandPort"),
    "UnixJsonCommandClient": (".commands", "UnixJsonCommandClient"),
    "SharedSnapshotPayload": (".shared_snapshot", "SharedSnapshotPayload"),
    "SharedSnapshotReader": (".shared_snapshot", "SharedSnapshotReader"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
