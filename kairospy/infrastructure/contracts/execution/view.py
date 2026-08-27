"""Execution owner-owned native current-view boundary."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any


def execution_indexed_environment_path(root: str | Path) -> Path:
    return (
        Path(root)
        / "views"
        / "v3"
        / "Execution"
        / "execution-main"
        / "epoch-1"
        / "current.lmdb"
    )


class ExecutionIndexedViewReader:
    """Thin lifecycle wrapper over the Execution contract PyO3 module."""

    def __init__(
        self,
        root: str | Path,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        native = import_module("kairospy._native_execution_contract")
        info = native.build_info()
        if info.api_version != 1 or info.owner != "Execution":
            raise RuntimeError("incompatible Execution native contract binding")
        self._native: Any = native
        self._args = (Path(root), workspace_id, launch_id, instance_id)
        self._reader: Any | None = None

    def _open(self) -> Any:
        if self._reader is None:
            self._reader = self._native.ExecutionCurrentView(*self._args)
        return self._reader

    def orders(self) -> tuple[Any, ...]:
        return tuple(self._open().orders())

    def get_order(self, order_id: str) -> Any | None:
        return self._open().get_order(order_id)

    def intents(self) -> tuple[Any, ...]:
        return tuple(self._open().intents())

    def get_intent(self, intent_id: str) -> Any | None:
        return self._open().get_intent(intent_id)

    def commitments(self) -> tuple[Any, ...]:
        return tuple(self._open().commitments())

    def algorithm_runs(self) -> tuple[Any, ...]:
        return tuple(self._open().algorithm_runs())

    def risk_reservations(self) -> tuple[Any, ...]:
        return tuple(self._open().risk_reservations())

    def unknown_remote_orders(self) -> tuple[Any, ...]:
        return tuple(self._open().unknown_remote_orders())

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


__all__ = ["ExecutionIndexedViewReader", "execution_indexed_environment_path"]
