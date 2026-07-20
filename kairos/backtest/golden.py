"""Compatibility exports for the renamed SPXW reference pipeline module."""

from .spxw_reference_pipeline import build_spxw_reference_pipeline

build_spxw_golden_pipeline = build_spxw_reference_pipeline

__all__ = ["build_spxw_reference_pipeline", "build_spxw_golden_pipeline"]
