"""Account v2 current-view contract and mmap reader."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys
from typing import Any

from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


class AccountViewKind(str, Enum):
    CURRENT = "current"
    OBSERVED_ORDERS = "observed-orders"


@dataclass(frozen=True, slots=True)
class AccountViewKey:
    account_runtime_id: str
    account_id: str
    kind: AccountViewKind = AccountViewKind.CURRENT

    def __post_init__(self) -> None:
        if not self.account_runtime_id.strip() or not self.account_id.strip():
            raise ValueError("Account view identity is incomplete")

    def canonical_key(self) -> str:
        return (
            f"runtime={self.account_runtime_id};account={self.account_id};"
            f"view={self.kind.value}"
        )

    def resource_path(self, root: str | Path) -> Path:
        return account_view_path(root, self)


def account_view_path(root: str | Path, key: AccountViewKey) -> Path:
    return (
        Path(root)
        / "account"
        / "views"
        / _component(key.account_runtime_id)
        / _component(key.account_id)
        / key.kind.value
        / "current.snapshot"
    )


@dataclass(frozen=True, slots=True)
class AccountViewFrame:
    key: AccountViewKey
    generation: int
    payload: bytes
    value: Any


class AccountViewReader:
    def __init__(self, root: str | Path, key: AccountViewKey, *, retries: int = 8) -> None:
        self.key = key
        self._reader = SharedSnapshotReader(account_view_path(root, key), retries=retries)

    def read(self) -> AccountViewFrame:
        snapshot = self._reader.read()
        value = decode_view(snapshot.payload, self.key.kind)
        return AccountViewFrame(self.key, snapshot.generation, snapshot.payload, value)


def decode_view(payload: bytes, kind: AccountViewKind) -> Any:
    roots = {
        AccountViewKind.CURRENT: (b"AAV2", "AccountCurrentView"),
        AccountViewKind.OBSERVED_ORDERS: (b"AOV2", "ObservedOrdersCurrentView"),
    }
    identifier, root_name = roots[kind]
    if len(payload) < 8 or payload[4:8] != identifier:
        raise ValueError(
            f"invalid Account {kind.value} view identifier: expected {identifier!r}"
        )
    module = __import__(
        f"kairospy.infrastructure.transport.generated.kairos.account.v2.{root_name}",
        fromlist=[root_name],
    )
    return getattr(module, root_name).GetRootAs(payload, 0)


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if (byte < 128 and chr(byte).isalnum()) or byte in b"-_."
        else f"%{byte:02X}"
        for byte in value.encode()
    )


__all__ = ["AccountViewFrame", "AccountViewKey", "AccountViewKind", "AccountViewReader", "account_view_path", "decode_view"]
