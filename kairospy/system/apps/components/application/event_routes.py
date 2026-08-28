"""System-owned Aeron event-route allocation and leases."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import fcntl
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Literal, Mapping

from kairospy.infrastructure.protocol.generated_spec import (
    TRANSPORT_FINGERPRINT,
    TRANSPORT_SPEC_VERSION,
)
from kairospy.system.apps.workspace.application import InstanceWorkspace, Workspace


_PORT_MIN = 40_123
_PORT_MAX = 60_000


@dataclass(frozen=True, slots=True)
class EventTransportRoute:
    """One explicitly leased Aeron delivery boundary."""

    route_id: str
    scope: Literal["workspace", "instance"]
    aeron_dir: Path
    channel: str
    transport_spec_version: int
    transport_fingerprint: str
    workspace_id: str
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise ValueError("event route_id is required")
        if self.scope not in {"workspace", "instance"}:
            raise ValueError("event route scope must be workspace or instance")
        if not isinstance(self.aeron_dir, Path):
            object.__setattr__(self, "aeron_dir", Path(self.aeron_dir))
        if not self.channel.startswith("aeron:udp?endpoint=127.0.0.1:"):
            raise ValueError("System event route must use a leased localhost UDP endpoint")
        if self.transport_spec_version != TRANSPORT_SPEC_VERSION:
            raise ValueError("event route transport spec version is incompatible")
        if self.transport_fingerprint != TRANSPORT_FINGERPRINT:
            raise ValueError("event route transport fingerprint is incompatible")
        if not self.workspace_id.strip():
            raise ValueError("event route workspace_id is required")
        if self.scope == "workspace":
            if self.launch_id is not None or self.instance_id is not None:
                raise ValueError("workspace event route cannot carry instance identity")
        elif not self.launch_id or not self.instance_id:
            raise ValueError("instance event route requires launch_id and instance_id")

    @property
    def port(self) -> int:
        return int(self.channel.rsplit(":", 1)[1])

    def as_manifest(self) -> dict[str, object]:
        value = asdict(self)
        value["aeron_dir"] = str(self.aeron_dir)
        return value


def ensure_workspace_event_route(workspace: Workspace) -> EventTransportRoute:
    return _ensure_route(
        route_file=workspace.paths.child("run", "transport", "workspace-route.json"),
        route_key=f"workspace:{workspace.paths.root.resolve()}",
        route_id="workspace_shared",
        scope="workspace",
        workspace=workspace,
        launch_id=None,
        instance_id=None,
    )


def ensure_instance_event_route(instance: InstanceWorkspace) -> EventTransportRoute:
    return _ensure_route(
        route_file=instance.paths.child("run", "transport", "instance-route.json"),
        route_key=(
            f"instance:{instance.workspace.paths.root.resolve()}:"
            f"{instance.mode}:{instance.launch_id}:{instance.instance_id}"
        ),
        route_id="instance",
        scope="instance",
        workspace=instance.workspace,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
    )


def release_instance_event_route(instance: InstanceWorkspace) -> None:
    route_file = instance.paths.child("run", "transport", "instance-route.json")
    route_key = (
        f"instance:{instance.workspace.paths.root.resolve()}:"
        f"{instance.mode}:{instance.launch_id}:{instance.instance_id}"
    )
    registry, lock = _registry_paths()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        leases = _load_mapping(registry)
        leases.pop(route_key, None)
        _write_json(registry, leases)
        route_file.unlink(missing_ok=True)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def event_route_from_manifest(value: object) -> EventTransportRoute:
    if not isinstance(value, Mapping):
        raise RuntimeError("event route must be an object")
    try:
        scope_value = str(value["scope"])
        if scope_value == "workspace":
            scope: Literal["workspace", "instance"] = "workspace"
        elif scope_value == "instance":
            scope = "instance"
        else:
            raise ValueError("unsupported event route scope")
        return EventTransportRoute(
            route_id=str(value["route_id"]),
            scope=scope,
            aeron_dir=Path(str(value["aeron_dir"])),
            channel=str(value["channel"]),
            transport_spec_version=int(value["transport_spec_version"]),
            transport_fingerprint=str(value["transport_fingerprint"]),
            workspace_id=str(value["workspace_id"]),
            launch_id=(
                None if value.get("launch_id") is None else str(value["launch_id"])
            ),
            instance_id=(
                None
                if value.get("instance_id") is None
                else str(value["instance_id"])
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"invalid event route: {error}") from error


def _ensure_route(
    *,
    route_file: Path,
    route_key: str,
    route_id: str,
    scope: Literal["workspace", "instance"],
    workspace: Workspace,
    launch_id: str | None,
    instance_id: str | None,
) -> EventTransportRoute:
    registry, lock = _registry_paths()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        leases = _load_mapping(registry)
        _discard_missing_route_files(leases)
        existing = _read_route(route_file)
        lease = leases.get(route_key)
        if existing is not None:
            if (
                existing.route_id != route_id
                or existing.scope != scope
                or existing.workspace_id != workspace.workspace_id
                or existing.launch_id != launch_id
                or existing.instance_id != instance_id
                or existing.aeron_dir != workspace.paths.aeron_dir()
            ):
                raise RuntimeError(
                    f"persisted event route does not belong to {route_key}"
                )
            if lease is not None and _integer(lease.get("port"), "lease port") != existing.port:
                raise RuntimeError("event route file conflicts with the machine lease")
            if lease is None:
                _reject_port_conflict(leases, existing.port, route_key)
                leases[route_key] = _lease(route_file, existing.port)
                _write_json(registry, leases)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            return existing

        if lease is not None:
            raise RuntimeError(
                "event route record is missing while its owner runtime is active"
            )

        port = _allocate_port(leases)
        route = EventTransportRoute(
            route_id=route_id,
            scope=scope,
            aeron_dir=workspace.paths.aeron_dir(),
            channel=f"aeron:udp?endpoint=127.0.0.1:{port}",
            transport_spec_version=TRANSPORT_SPEC_VERSION,
            transport_fingerprint=TRANSPORT_FINGERPRINT,
            workspace_id=workspace.workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
        )
        _write_json(route_file, route.as_manifest())
        leases[route_key] = _lease(route_file, port)
        _write_json(registry, leases)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return route


def _registry_paths() -> tuple[Path, Path]:
    root = Path(tempfile.gettempdir()) / f"kairos-event-routes-{os.getuid()}"
    return root / "leases.json", root / "leases.lock"


def _read_route(path: Path) -> EventTransportRoute | None:
    if not path.is_file():
        return None
    try:
        return event_route_from_manifest(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, RuntimeError) as error:
        raise RuntimeError(f"invalid persisted event route {path}: {error}") from error


def _load_mapping(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid event route lease registry: {error}") from error
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, dict)
        for key, item in value.items()
    ):
        raise RuntimeError("event route lease registry must be an object")
    return value


def _discard_missing_route_files(leases: dict[str, dict[str, object]]) -> None:
    for key, value in list(leases.items()):
        route_file = value.get("route_file")
        if not isinstance(route_file, str):
            raise RuntimeError("event route lease route_file must be a path")
        path = Path(route_file)
        if path.is_file():
            continue
        runtime_root = path.parents[2] if len(path.parents) >= 3 else None
        if runtime_root is None or not _runtime_has_held_process_lock(runtime_root):
            leases.pop(key, None)


def _runtime_has_held_process_lock(runtime_root: Path) -> bool:
    """Confirm that a missing route record is not owned by a live process."""

    if not runtime_root.is_dir():
        return False
    for lock_path in runtime_root.rglob("process.lock"):
        try:
            with lock_path.open("a+") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                finally:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        except OSError:
            # An unreadable lock cannot prove that the owner stopped.
            return True
    return False


def _reject_port_conflict(
    leases: Mapping[str, Mapping[str, object]], port: int, route_key: str
) -> None:
    if any(
        key != route_key and _integer(value.get("port"), "lease port") == port
        for key, value in leases.items()
    ):
        raise RuntimeError(f"event route port {port} is leased by another route")


def _allocate_port(leases: Mapping[str, Mapping[str, object]]) -> int:
    leased = {
        _integer(value.get("port"), "lease port") for value in leases.values()
    }
    for port in range(_PORT_MIN, _PORT_MAX + 1):
        if port in leased:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free System event-route UDP endpoint is available")


def _lease(route_file: Path, port: int) -> dict[str, object]:
    return {"route_file": str(route_file), "port": port}


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"event route {name} must be an integer")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


__all__ = [
    "EventTransportRoute",
    "ensure_instance_event_route",
    "ensure_workspace_event_route",
    "event_route_from_manifest",
    "release_instance_event_route",
]
