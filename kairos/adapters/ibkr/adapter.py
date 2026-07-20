from __future__ import annotations

"""Compatibility exports for the split IBKR connector modules."""

from .account_gateway import IbkrAccountAdapter, IbkrAccountGateway
from .execution_gateway import (
    IBKR_EXECUTION_CAPABILITIES,
    IbkrExecutionAdapter,
    IbkrExecutionGateway,
    normalize_ibkr_execution,
)
from .market_data_client import IBKR_MARKET_DATA_CAPABILITIES, IbkrMarketDataAdapter, IbkrMarketDataClient
from .reference_data import IBKR_REFERENCE_CAPABILITIES, IbkrReferenceAdapter, IbkrReferenceDataClient
from .session import IbkrSession
