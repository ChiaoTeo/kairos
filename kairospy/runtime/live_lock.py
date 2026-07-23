from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import errno
import fcntl
import json
import os
from pathlib import Path

from kairospy.infrastructure.storage.codec import to_primitive

from .live_registry import LiveRunProcessIdentity


_HELD_LOCKS: dict[Path, LiveRunProcessIdentity] = {}


@dataclass(frozen=True, slots=True)
class LiveRunLockOwner:
    run_id: str | None
    process_id: str | None
    pid: int | None
    host: str | None
    started_at: str | None
    heartbeat_at: str | None
    config_hash: str | None

    @classmethod
    def from_payload(cls, payload: object) -> "LiveRunLockOwner":
        data = payload if isinstance(payload, dict) else {}
        pid = data.get("pid")
        return cls(
            run_id=_optional_text(data.get("run_id")),
            process_id=_optional_text(data.get("process_id")),
            pid=int(pid) if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()) else None,
            host=_optional_text(data.get("host")),
            started_at=_optional_text(data.get("started_at")),
            heartbeat_at=_optional_text(data.get("heartbeat_at")),
            config_hash=_optional_text(data.get("config_hash")),
        )

    def summary(self) -> str:
        parts = []
        if self.run_id:
            parts.append(f"run_id={self.run_id}")
        if self.pid is not None:
            parts.append(f"pid={self.pid}")
        if self.host:
            parts.append(f"host={self.host}")
        if self.heartbeat_at:
            parts.append(f"heartbeat_at={self.heartbeat_at}")
        return ", ".join(parts) or "unknown owner"


class LiveRunFileLock:
    """Best-effort local process lock for one live run-id."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file = None
        self._owner: LiveRunProcessIdentity | None = None

    @property
    def acquired(self) -> bool:
        return self._file is not None

    def acquire(self, identity: LiveRunProcessIdentity, *, at: datetime) -> None:
        if self.acquired:
            return
        lock_key = self.path.resolve()
        if lock_key in _HELD_LOCKS:
            owner = _HELD_LOCKS[lock_key]
            raise RuntimeError(
                f"live run {identity.run_id!r} is already locked by another process"
                f" (run_id={owner.run_id}, pid={owner.pid}, host={owner.host})"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            owner = self.owner()
            handle.close()
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise RuntimeError(
                    f"live run {identity.run_id!r} is already locked by another process"
                    f" ({owner.summary()})"
                ) from error
            raise
        self._file = handle
        self._owner = identity
        _HELD_LOCKS[lock_key] = identity
        self.heartbeat(at=at)

    def heartbeat(self, *, at: datetime) -> None:
        if self._file is None or self._owner is None:
            return
        payload = {
            **self._owner.manifest(),
            "heartbeat_at": at.isoformat(),
            "lock_path": str(self.path),
        }
        self._file.seek(0)
        self._file.truncate()
        json.dump(to_primitive(payload), self._file, ensure_ascii=False, sort_keys=True)
        self._file.write("\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        lock_key = self.path.resolve()
        self._file = None
        self._owner = None
        _HELD_LOCKS.pop(lock_key, None)
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            pass
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def owner(self) -> LiveRunLockOwner:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return LiveRunLockOwner.from_payload({})
        if not text:
            return LiveRunLockOwner.from_payload({})
        try:
            return LiveRunLockOwner.from_payload(json.loads(text))
        except json.JSONDecodeError:
            return LiveRunLockOwner.from_payload({})


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["LiveRunFileLock", "LiveRunLockOwner"]
