"""Launch-owned wrapper around a strategy runtime session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from kairospy.application.support.runtime.application.engine import RuntimeResult
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message

if TYPE_CHECKING:
    from kairospy.application.support.runtime.application.engine import RuntimeSession


@dataclass(frozen=True, slots=True)
class LaunchRuntimeResult:
    launch_id: str
    mode: object
    runtime: RuntimeResult
    views: ViewStore
    intents: object | None = None


@dataclass(frozen=True, slots=True)
class LaunchRuntimeSession:
    launch_id: str
    mode: object
    session: "RuntimeSession"
    started_at: object | None = None

    @property
    def views(self) -> ViewStore:
        return self.session.views

    @property
    def is_finished(self) -> bool:
        return self.session.is_finished

    def observe(self, event: Message):
        return self.session.observe(event)

    def process(self, event: Message, *, hook: str | None = None):
        return self.session.process(event, hook=hook)

    def finish(self) -> RuntimeResult:
        return self.session.finish()

    def stop(self) -> None:
        self.session.stop()


__all__ = ["LaunchRuntimeResult", "LaunchRuntimeSession"]
