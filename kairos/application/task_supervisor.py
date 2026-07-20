from __future__ import annotations

"""Compatibility exports for the renamed service supervisor module."""

from .service_supervisor import (
    AsyncServiceSupervisor,
    AsyncTaskSupervisor,
    ManagedServiceSnapshot,
    ManagedServiceSpec,
    ManagedServiceStatus,
    ManagedTaskSnapshot,
    ManagedTaskSpec,
    ManagedTaskStatus,
    ServiceCriticality,
    ServiceFault,
    TaskCriticality,
    TaskFault,
)

__all__ = [
    "AsyncTaskSupervisor",
    "AsyncServiceSupervisor",
    "ManagedServiceSnapshot",
    "ManagedServiceSpec",
    "ManagedServiceStatus",
    "ManagedTaskSnapshot",
    "ManagedTaskSpec",
    "ManagedTaskStatus",
    "ServiceCriticality",
    "ServiceFault",
    "TaskCriticality",
    "TaskFault",
]
