"""Compatibility exports for the renamed treasury ledger posting module."""

from .ledger_posting import TreasuryLedgerPostingService, TreasuryService

__all__ = ["TreasuryLedgerPostingService", "TreasuryService"]
