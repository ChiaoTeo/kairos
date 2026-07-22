from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
import platform
from uuid import uuid4

from kairospy import __version__
from kairospy.infrastructure.storage.codec import to_primitive


@dataclass(frozen=True, slots=True)
class LiveRunProcessIdentity:
    run_id: str
    runtime_id: str
    process_id: str
    pid: int
    host: str
    version: str
    config_hash: str
    started_at: datetime

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        runtime_id: str,
        started_at: datetime,
        config_hash: str = "unknown",
        version: str = __version__,
    ) -> "LiveRunProcessIdentity":
        _aware(started_at)
        host = platform.node() or "localhost"
        pid = os.getpid()
        process_id = f"{host}:{pid}:{started_at.isoformat()}:{uuid4()}"
        return cls(run_id, runtime_id, process_id, pid, host, version, config_hash, started_at)

    def manifest(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "process_id": self.process_id,
            "pid": self.pid,
            "host": self.host,
            "version": self.version,
            "config_hash": self.config_hash,
            "started_at": self.started_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class LiveRunHeartbeat:
    identity: LiveRunProcessIdentity
    heartbeat_at: datetime
    observed_state: str
    desired_state: str
    state: dict[str, object]

    def manifest(self) -> dict[str, object]:
        return {
            **self.identity.manifest(),
            "heartbeat_at": self.heartbeat_at.isoformat(),
            "observed_state": self.observed_state,
            "desired_state": self.desired_state,
            "state": self.state,
        }


class LiveRunRegistry:
    """Durable live run process identity and heartbeat view."""

    def __init__(self, store: object) -> None:
        if not hasattr(store, "transaction"):
            raise ValueError("live run registry requires a transactional runtime store")
        self.store = store

    def heartbeat(
        self,
        identity: LiveRunProcessIdentity,
        *,
        observed_state: str,
        desired_state: str,
        state: dict[str, object] | None,
        at: datetime,
    ) -> LiveRunHeartbeat:
        _aware(at)
        heartbeat = LiveRunHeartbeat(
            identity,
            at,
            _required_text(observed_state, "observed_state"),
            _required_text(desired_state, "desired_state"),
            dict(state or {}),
        )
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO runtime_heartbeats(
                    run_id, runtime_id, process_id, pid, host, version, config_hash,
                    started_at, heartbeat_at, observed_state, desired_state, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    runtime_id = excluded.runtime_id,
                    process_id = excluded.process_id,
                    pid = excluded.pid,
                    host = excluded.host,
                    version = excluded.version,
                    config_hash = excluded.config_hash,
                    started_at = excluded.started_at,
                    heartbeat_at = excluded.heartbeat_at,
                    observed_state = excluded.observed_state,
                    desired_state = excluded.desired_state,
                    state_json = excluded.state_json""",
                (
                    identity.run_id,
                    identity.runtime_id,
                    identity.process_id,
                    identity.pid,
                    identity.host,
                    identity.version,
                    identity.config_hash,
                    identity.started_at.isoformat(),
                    at.isoformat(),
                    heartbeat.observed_state,
                    heartbeat.desired_state,
                    _encode(heartbeat.state),
                ),
            )
        return heartbeat

    def heartbeat_for(self, run_id: str) -> LiveRunHeartbeat | None:
        run_id = _required_text(run_id, "run_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_heartbeats WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _heartbeat_record(row) if row is not None else None

    def status(
        self,
        run_id: str,
        *,
        at: datetime,
        stale_after_seconds: float = 5.0,
    ) -> dict[str, object] | None:
        _aware(at)
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        heartbeat = self.heartbeat_for(run_id)
        if heartbeat is None:
            return None
        age = (at - heartbeat.heartbeat_at).total_seconds()
        status = "stale" if age > stale_after_seconds else heartbeat.observed_state
        return {
            "status": status,
            "stale": status == "stale",
            "heartbeat_age_seconds": age,
            "stale_after_seconds": stale_after_seconds,
            **heartbeat.manifest(),
        }


def _heartbeat_record(row: object) -> LiveRunHeartbeat:
    identity = LiveRunProcessIdentity(
        str(row["run_id"]),
        str(row["runtime_id"]),
        str(row["process_id"]),
        int(row["pid"]),
        str(row["host"]),
        str(row["version"]),
        str(row["config_hash"]),
        datetime.fromisoformat(row["started_at"]),
    )
    state = json.loads(row["state_json"])
    return LiveRunHeartbeat(
        identity,
        datetime.fromisoformat(row["heartbeat_at"]),
        str(row["observed_state"]),
        str(row["desired_state"]),
        state if isinstance(state, dict) else {"value": state},
    )


def _required_text(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text


def _encode(value: object) -> str:
    return json.dumps(to_primitive(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("live run heartbeat timestamps must be timezone-aware")


__all__ = [
    "LiveRunHeartbeat",
    "LiveRunProcessIdentity",
    "LiveRunRegistry",
]
