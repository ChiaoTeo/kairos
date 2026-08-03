from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
from typing import Mapping

from kairospy.domain.account import AccountIdentity


DEFAULT_STALE_AFTER_SECONDS = 60.0


class AccountLeaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AccountLeaseRecord:
    account_key: str
    broker: str
    account_id: str
    environment: str
    launch_id: str
    launch_instance_id: str
    mode: str
    pid: int
    host: str
    acquired_at: str
    heartbeat_at: str
    path: Path
    stale: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "account_key": self.account_key,
            "broker": self.broker,
            "account_id": self.account_id,
            "environment": self.environment,
            "launch_id": self.launch_id,
            "launch_instance_id": self.launch_instance_id,
            "mode": self.mode,
            "pid": self.pid,
            "host": self.host,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
            "path": str(self.path),
            "stale": self.stale,
        }


@dataclass(frozen=True, slots=True)
class AccountLease:
    manager: "AccountLeaseManager"
    record: AccountLeaseRecord

    def heartbeat(self) -> None:
        self.manager.heartbeat(self.record.account_key, launch_instance_id=self.record.launch_instance_id)

    def release(self) -> None:
        self.manager.release(self.record.account_key, launch_instance_id=self.record.launch_instance_id)


class AccountLeaseSet:
    def __init__(self, leases: tuple[AccountLease, ...]) -> None:
        self.leases = leases

    def heartbeat(self) -> None:
        for lease in self.leases:
            lease.heartbeat()

    def release(self) -> None:
        for lease in reversed(self.leases):
            lease.release()

    def __enter__(self) -> "AccountLeaseSet":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


class AccountLeaseManager:
    def __init__(self, root: str | Path, *, stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS) -> None:
        self.root = Path(root).expanduser()
        self.stale_after_seconds = stale_after_seconds

    def acquire_many(
        self,
        accounts: tuple[tuple[AccountIdentity, str], ...],
        *,
        launch_id: str,
        launch_instance_id: str,
        mode: str,
        pid: int | None = None,
    ) -> AccountLeaseSet:
        leases: list[AccountLease] = []
        try:
            seen: set[str] = set()
            for identity, environment in accounts:
                key = account_lock_key(identity)
                if key in seen:
                    continue
                seen.add(key)
                leases.append(
                    self.acquire(
                        identity,
                        environment=environment,
                        launch_id=launch_id,
                        launch_instance_id=launch_instance_id,
                        mode=mode,
                        pid=pid,
                    )
                )
        except Exception:
            AccountLeaseSet(tuple(leases)).release()
            raise
        return AccountLeaseSet(tuple(leases))

    def acquire(
        self,
        identity: AccountIdentity,
        *,
        environment: str,
        launch_id: str,
        launch_instance_id: str,
        mode: str,
        pid: int | None = None,
    ) -> AccountLease:
        self.root.mkdir(parents=True, exist_ok=True)
        key = account_lock_key(identity)
        path = self.root / key
        record = self._record(
            key,
            identity,
            environment=environment,
            launch_id=launch_id,
            launch_instance_id=launch_instance_id,
            mode=mode,
            pid=os.getpid() if pid is None else pid,
            path=path,
        )
        try:
            path.mkdir()
        except FileExistsError as error:
            existing = self.get(key)
            if existing is not None and existing.launch_instance_id == launch_instance_id:
                self._write_record(path, record)
                return AccountLease(self, record)
            if existing is not None and existing.stale:
                self.release(key, force=True, stale_only=True)
                path.mkdir()
            else:
                owner = existing.to_dict() if existing is not None else {"path": str(path)}
                raise AccountLeaseError(f"account {key} trading is already leased: {owner}") from error
        self._write_record(path, record)
        return AccountLease(self, record)

    def heartbeat(self, account_key: str, *, launch_instance_id: str) -> None:
        path = self.root / account_key
        record = self.get(account_key)
        if record is None:
            return
        if record.launch_instance_id != launch_instance_id:
            raise AccountLeaseError(f"account {account_key} is leased by another instance: {record.launch_instance_id}")
        self._write_record(path, _replace_heartbeat(record))

    def release(
        self,
        account_key: str,
        *,
        launch_instance_id: str | None = None,
        force: bool = False,
        stale_only: bool = False,
    ) -> bool:
        path = self.root / account_key
        record = self.get(account_key)
        if record is None:
            return False
        if stale_only and not record.stale:
            raise AccountLeaseError(f"account {account_key} trading lease is not stale")
        if not force and launch_instance_id is not None and record.launch_instance_id != launch_instance_id:
            raise AccountLeaseError(f"account {account_key} trading is leased by another instance: {record.launch_instance_id}")
        shutil.rmtree(path)
        return True

    def list(self) -> tuple[AccountLeaseRecord, ...]:
        if not self.root.exists():
            return ()
        records = [record for path in sorted(self.root.iterdir()) if path.is_dir() for record in [self._read_record(path)] if record is not None]
        return tuple(records)

    def get(self, account_key: str) -> AccountLeaseRecord | None:
        return self._read_record(self.root / account_key)

    def _record(
        self,
        key: str,
        identity: AccountIdentity,
        *,
        environment: str,
        launch_id: str,
        launch_instance_id: str,
        mode: str,
        pid: int,
        path: Path,
    ) -> AccountLeaseRecord:
        now = datetime.now(timezone.utc).isoformat()
        return AccountLeaseRecord(
            account_key=key,
            broker=str(identity.broker),
            account_id=str(identity.account_id),
            environment=environment,
            launch_id=launch_id,
            launch_instance_id=launch_instance_id,
            mode=mode,
            pid=pid,
            host=socket.gethostname(),
            acquired_at=now,
            heartbeat_at=now,
            path=path,
            stale=False,
        )

    def _read_record(self, path: Path) -> AccountLeaseRecord | None:
        owner = path / "owner.json"
        if not owner.exists():
            return None
        try:
            payload = json.loads(owner.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, Mapping):
            return None
        record = AccountLeaseRecord(
            account_key=str(payload.get("account_key") or path.name),
            broker=str(payload.get("broker") or ""),
            account_id=str(payload.get("account_id") or ""),
            environment=str(payload.get("environment") or ""),
            launch_id=str(payload.get("launch_id") or ""),
            launch_instance_id=str(payload.get("launch_instance_id") or ""),
            mode=str(payload.get("mode") or ""),
            pid=int(payload.get("pid") or 0),
            host=str(payload.get("host") or ""),
            acquired_at=str(payload.get("acquired_at") or ""),
            heartbeat_at=str(payload.get("heartbeat_at") or ""),
            path=path,
            stale=False,
        )
        return _replace_stale(record, stale=self._is_stale(record))

    def _write_record(self, path: Path, record: AccountLeaseRecord) -> None:
        payload = record.to_dict()
        payload.pop("path", None)
        payload.pop("stale", None)
        _write_json(path / "owner.json", payload)

    def _is_stale(self, record: AccountLeaseRecord) -> bool:
        try:
            heartbeat = datetime.fromisoformat(record.heartbeat_at)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        if age <= self.stale_after_seconds:
            return False
        return not _pid_alive(record.pid)


def account_lock_key(identity: AccountIdentity) -> str:
    return ".".join(_key_part(part) for part in (identity.broker, identity.account_id) if part)


def _replace_heartbeat(record: AccountLeaseRecord) -> AccountLeaseRecord:
    return AccountLeaseRecord(
        **(record.to_dict() | {"heartbeat_at": datetime.now(timezone.utc).isoformat(), "path": record.path, "stale": False})
    )


def _replace_stale(record: AccountLeaseRecord, *, stale: bool) -> AccountLeaseRecord:
    return AccountLeaseRecord(**(record.to_dict() | {"stale": stale, "path": record.path}))


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = [
    "AccountLease",
    "AccountLeaseError",
    "AccountLeaseManager",
    "AccountLeaseRecord",
    "AccountLeaseSet",
    "account_lock_key",
]
