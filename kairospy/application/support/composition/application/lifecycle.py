from __future__ import annotations

from pathlib import Path

from kairospy.application.support.launch.application.lifecycle import TradingLifecycle
from kairospy.infrastructure.persistence.application.runtime_state import JsonLiveRuntimeStateStore


class LiveConfiguredLifecycle:
    """Composition-selected lifecycle that persists live business state."""

    def __init__(self, state_path: Path | str, *, account: object, coordinator: object) -> None:
        self.account = account
        self.coordinator = coordinator
        self.state_store = JsonLiveRuntimeStateStore(state_path)

    def prepare(self) -> None:
        snapshot = self.state_store.load()
        if snapshot is not None:
            snapshot.restore_execution_into(self.coordinator)  # type: ignore[arg-type]
            private_stream = getattr(self.account, "private_stream_state", None)
            if private_stream is not None:
                private_stream.restore_checkpoint(snapshot.private_stream)
        refresh = getattr(self.account, "refresh", None)
        if callable(refresh):
            refresh()

    def complete(self) -> None:
        private_stream = getattr(self.account, "private_stream_state", None)
        if private_stream is None:
            return
        self.state_store.save(  # type: ignore[arg-type]
            self.coordinator,
            private_stream.checkpoint(),
        )


__all__ = ["LiveConfiguredLifecycle"]
