# pyright: reportUnsupportedDunderAll=false

"""Python adapters for versioned Kairos process-boundary transports.

Exports are resolved lazily so a contract client can be imported without
eagerly importing the strategy application and recreating an initialization
cycle.
"""

from importlib import import_module

_EXPORTS = {
    "UnixJsonCommandClient": (".commands", "UnixJsonCommandClient"),
    "UnixJsonRpcClient": (".commands", "UnixJsonRpcClient"),
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
