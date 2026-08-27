"""Account owner-owned native current-view boundary."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any


def account_indexed_environment_path(root: str | Path, account_id: str) -> Path:
    return (
        Path(root)
        / "views"
        / "v3"
        / "Account"
        / f"account-{account_id}"
        / "epoch-1"
        / "current.lmdb"
    )


class AccountIndexedViewReader:
    """Thin lifecycle wrapper over the Account contract PyO3 module."""

    def __init__(self, root: str | Path, *, account_id: str, workspace_id: str, launch_id: str | None, instance_id: str | None) -> None:
        native = import_module("kairospy._native_account_contract")
        info = native.build_info()
        if info.api_version != 1 or info.owner != "Account":
            raise RuntimeError("incompatible Account native contract binding")
        self.path = account_indexed_environment_path(root, account_id)
        self._native: Any = native
        self._args = (Path(root), account_id, workspace_id, launch_id, instance_id)
        self._reader: Any | None = None

    def _open(self) -> Any:
        if self._reader is None:
            self._reader = self._native.AccountCurrentView(*self._args)
        return self._reader

    def snapshot(self) -> Any:
        return self._open().snapshot()

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


__all__ = ["AccountIndexedViewReader", "account_indexed_environment_path"]
