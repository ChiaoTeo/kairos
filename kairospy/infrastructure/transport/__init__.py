# pyright: reportUnsupportedDunderAll=false

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
    "MarketViewAccess": (".market", "MarketViewAccess"),
    "AeronMarketEventSource": (".market", "AeronMarketEventSource"),
    "QuoteView": (".market", "QuoteView"),
    "TradeView": (".market", "TradeView"),
    "UnixMarketEventStream": (".market", "UnixMarketEventStream"),
    "UnixJsonCommandClient": (".commands", "UnixJsonCommandClient"),
    "UnixJsonRpcClient": (".commands", "UnixJsonRpcClient"),
    "AeronReferenceEventSource": (".reference", "AeronReferenceEventSource"),
    "AeronCapitalEventSource": (".capital", "AeronCapitalEventSource"),
    "decode_reference_event": (".reference", "decode_reference_event"),
    "IndexedViewMetadata": (".indexed_view", "IndexedViewMetadata"),
    "IndexedViewReader": (".indexed_view", "IndexedViewReader"),
    "IndexedViewSchema": (".indexed_view", "IndexedViewSchema"),
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
