from __future__ import annotations


class CcxtConnectorError(RuntimeError):
    """Raised when a CCXT exchange response cannot be normalized safely."""


class CcxtDependencyUnavailable(CcxtConnectorError):
    """Raised when the optional ccxt dependency is not installed."""
