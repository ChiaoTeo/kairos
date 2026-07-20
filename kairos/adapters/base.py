"""Compatibility exports for venue port contracts.

New code should import from :mod:`kairos.ports`.
"""

from kairos.ports import (
    AccountPort,
    AccountState,
    ComboExecutionPort,
    ComboLegRequest,
    ComboOrderRequest,
    CorporateActionPort,
    Environment,
    ExecutionPort,
    FundingSettlementPort,
    MarketDataPort,
    OrderAck,
    OrderRecoveryPort,
    OrderRequest,
    RecoveredExecution,
    ReferenceDataPort,
    ReferenceDataRequest,
    VenueBalance,
    VenueOrderRecovery,
    VenueOrderStatus,
)

ReferenceDataAdapter = ReferenceDataPort
MarketDataAdapter = MarketDataPort
ExecutionAdapter = ExecutionPort
ComboExecutionAdapter = ComboExecutionPort
AccountAdapter = AccountPort
OrderRecoveryAdapter = OrderRecoveryPort
CorporateActionAdapter = CorporateActionPort
FundingSettlementAdapter = FundingSettlementPort

__all__ = [
    "AccountAdapter",
    "AccountPort",
    "AccountState",
    "ComboExecutionAdapter",
    "ComboExecutionPort",
    "ComboLegRequest",
    "ComboOrderRequest",
    "CorporateActionAdapter",
    "CorporateActionPort",
    "Environment",
    "ExecutionAdapter",
    "ExecutionPort",
    "FundingSettlementAdapter",
    "FundingSettlementPort",
    "MarketDataAdapter",
    "MarketDataPort",
    "OrderAck",
    "OrderRecoveryAdapter",
    "OrderRecoveryPort",
    "OrderRequest",
    "RecoveredExecution",
    "ReferenceDataAdapter",
    "ReferenceDataPort",
    "ReferenceDataRequest",
    "VenueBalance",
    "VenueOrderRecovery",
    "VenueOrderStatus",
]
