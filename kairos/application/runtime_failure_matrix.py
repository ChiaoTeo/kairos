from __future__ import annotations

"""Compatibility exports for the renamed runtime failure policy module."""

from .runtime_failure_policy import (
    FAILURE_MATRIX_ID,
    RUNTIME_FAILURE_POLICY_ID,
    run_runtime_failure_matrix,
    run_runtime_failure_policy,
)

__all__ = [
    "FAILURE_MATRIX_ID",
    "RUNTIME_FAILURE_POLICY_ID",
    "run_runtime_failure_matrix",
    "run_runtime_failure_policy",
]
