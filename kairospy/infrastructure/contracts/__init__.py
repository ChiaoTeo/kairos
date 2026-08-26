"""Python read/write facades for business-module contracts.

These facades share the Rust contract's transport semantics.  They are kept
under infrastructure rather than application so callers do not accidentally
depend on a process client when they only need a snapshot or event contract.
"""

from .base import (
    CommandEnvelope,
    QueryEnvelope,
)

__all__ = [
    "CommandEnvelope",
    "QueryEnvelope",
]
