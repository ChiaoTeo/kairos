from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.runtime.application.launch import RuntimeLaunchSession
from kairospy.application.support.runtime.application.projection import RuntimeProjector
from kairospy.application.support.runtime.domain.components import RuntimeComponents
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.services.orchestration.kernel import RuntimeKernel
from kairospy.application.support.runtime.services.orchestration.state import RuntimeStores, RuntimeStep
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.domain.intent import IntentJournal


@dataclass(frozen=True, slots=True)
class RuntimeEngineSpec:
    launch_id: str
    mode: RuntimeMode | str
    strategy: Strategy
    components: RuntimeComponents | None = None
    stores: RuntimeStores | None = None
    processors: RuntimeProjector | None = None

    def __post_init__(self) -> None:
        if not self.launch_id.strip():
            raise ValueError("launch_id is required")
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


def create_runtime_launch_session(spec: RuntimeEngineSpec) -> RuntimeLaunchSession:
    components = spec.components or RuntimeComponents()
    stores = spec.stores or RuntimeStores(intents=IntentJournal())
    if spec.processors is None:
        raise ValueError("runtime processors must be supplied by composition")
    kernel = RuntimeKernel(
        spec.strategy,
        components=components,
        stores=stores,
        processors=spec.processors,
    )
    return RuntimeLaunchSession(
        launch_id=spec.launch_id,
        mode=spec.mode,
        kernel=kernel,
        session=kernel.start(),
    )


__all__ = [
    "RuntimeEngineSpec",
    "RuntimeStores",
    "RuntimeStep",
    "create_runtime_launch_session",
]
