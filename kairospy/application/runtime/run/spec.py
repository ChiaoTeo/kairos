from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from kairospy.application.runtime.model import RuntimeMode
from kairospy.application.runtime.protocol import RuntimeEnvelope, RuntimeEventLine
from kairospy.application.runtime.services import AccountService, MarketDataService, ReferenceService
from kairospy.application.runtime.services.component import RuntimeComponentProvider, RuntimeViewPublisher
from kairospy.application.strategy import ControlJournal, Strategy
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore


@dataclass(frozen=True, slots=True)
class RuntimeRunSpec:
    run_id: str
    mode: RuntimeMode | str
    strategy: Strategy
    source: RuntimeEventLine | None = None
    state: Mapping[str, object] = field(default_factory=dict)
    intents: IntentJournal | None = None
    controls: ControlJournal | None = None
    views: ViewStore | None = None
    data: MarketDataService | None = None
    account: AccountService | None = None
    reference: ReferenceService | None = None
    execution: ExecutionCoordinator | None = None
    providers: tuple[RuntimeComponentProvider, ...] = ()
    components: tuple[RuntimeViewPublisher, ...] = ()
    pre_events: tuple[RuntimeEnvelope, ...] = ()
    started_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


__all__ = ["RuntimeRunSpec"]
