from __future__ import annotations

"""Compatibility exports for the renamed runtime reference artifact module."""

from .runtime_reference_artifact import (
    GOLDEN_SCHEMA_VERSION,
    GOLDEN_SCENARIO_ID,
    RUNTIME_REFERENCE_SCHEMA_VERSION,
    RUNTIME_REFERENCE_SCENARIO_ID,
    STARTED_AT,
    RuntimeGoldenResult,
    RuntimeReferenceArtifactResult,
    _catalog,
    run_runtime_golden,
    run_runtime_reference_artifact,
)

__all__ = [
    "GOLDEN_SCHEMA_VERSION",
    "GOLDEN_SCENARIO_ID",
    "RUNTIME_REFERENCE_SCHEMA_VERSION",
    "RUNTIME_REFERENCE_SCENARIO_ID",
    "STARTED_AT",
    "RuntimeGoldenResult",
    "RuntimeReferenceArtifactResult",
    "_catalog",
    "run_runtime_golden",
    "run_runtime_reference_artifact",
]
