from __future__ import annotations

from .accounts import AccountRegistry, RuntimeAccount
from .journal import RunAccountJournal
from .registry import RunRecord, RunRegistry, list_run_daemons


def __getattr__(name: str) -> object:
    if name in {"RunDaemonResult", "RunDaemonService"}:
        from .daemon import RunDaemonResult, RunDaemonService

        return {"RunDaemonResult": RunDaemonResult, "RunDaemonService": RunDaemonService}[name]
    raise AttributeError(name)


__all__ = [
    "AccountRegistry",
    "RunAccountJournal",
    "RunDaemonResult",
    "RunDaemonService",
    "RunRecord",
    "RunRegistry",
    "RuntimeAccount",
    "list_run_daemons",
]
