"""Compatibility exports for the renamed US equity momentum diagnostics module."""

from .us_equity_momentum_diagnostics import UsEquityMomentumDiagnostics

UsEquityMomentumReadiness = UsEquityMomentumDiagnostics

__all__ = ["UsEquityMomentumDiagnostics", "UsEquityMomentumReadiness"]
