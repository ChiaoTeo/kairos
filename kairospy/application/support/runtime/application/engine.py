from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.runtime.services.orchestration.kernel import RuntimeKernel, RuntimeSession
from kairospy.application.support.runtime.services.orchestration.state import Callback, RuntimeCycle, RuntimeFrame, RuntimeResult, RuntimeStores


@dataclass(frozen=True, slots=True)
class RuntimeEngineSpec:
    program_id: str
    dispatcher_factory: object
    system_call: object | None = None
    stores: RuntimeStores | None = None
    reference: object | None = None


def create_runtime_kernel(spec: RuntimeEngineSpec) -> RuntimeKernel:
    stores = spec.stores or RuntimeStores()
    kernel = RuntimeKernel(
        spec.dispatcher_factory,
        program_id=spec.program_id,
        system_call=spec.system_call,
        stores=stores,
        reference=spec.reference,
    )
    return kernel


def create_runtime_session(spec: RuntimeEngineSpec):
    """Create the public runtime session without exposing orchestration services."""
    return create_runtime_kernel(spec).start()


__all__ = [
    "RuntimeEngineSpec",
    "RuntimeStores",
    "RuntimeCycle",
    "RuntimeSession",
    "RuntimeFrame",
    "RuntimeResult",
    "Callback",
    "create_runtime_session",
]
