"""Compatibility shim for the former backtest service module."""

from __future__ import annotations

from .experiment_runner import BacktestExperimentRunner, BacktestService

__all__ = ["BacktestExperimentRunner", "BacktestService"]
