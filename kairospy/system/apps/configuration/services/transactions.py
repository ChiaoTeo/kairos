"""Recoverable multi-file transactions for Workspace configuration metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import uuid

from kairospy.system.domain.workspace import Workspace


@dataclass(slots=True)
class WorkspaceConfigurationTransaction:
    """Commit several secret-free config files as one recoverable unit.

    The journal contains only Workspace-relative paths and file modes. Secret
    values must be staged separately in unreferenced, versioned files and added
    to ``cleanup_on_rollback``.
    """

    workspace: Workspace
    label: str
    transaction_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _writes: list[tuple[Path, bytes, int]] = field(default_factory=list)
    _rollback_cleanup: list[Path] = field(default_factory=list)
    _commit_cleanup: list[Path] = field(default_factory=list)

    def stage_text(self, target: Path, content: str, *, mode: int = 0o600) -> None:
        self.stage_bytes(target, content.encode("utf-8"), mode=mode)

    def stage_bytes(self, target: Path, content: bytes, *, mode: int = 0o600) -> None:
        resolved = _workspace_path(self.workspace, target)
        if any(existing == resolved for existing, _content, _mode in self._writes):
            raise ValueError(f"transaction target is already staged: {resolved}")
        self._writes.append((resolved, bytes(content), mode))

    def cleanup_on_rollback(self, path: Path) -> None:
        self._rollback_cleanup.append(_workspace_path(self.workspace, path))

    def cleanup_on_commit(self, path: Path) -> None:
        self._commit_cleanup.append(_workspace_path(self.workspace, path))

    def commit(self) -> None:
        if not self._writes:
            raise ValueError("configuration transaction has no writes")
        lock = _TransactionLock(self.workspace)
        with lock:
            _recover_configuration_transactions(self.workspace)
            root = _transaction_root(self.workspace) / self.transaction_id
            staged_root = root / "staged"
            backup_root = root / "backup"
            try:
                staged_root.mkdir(parents=True, exist_ok=False)
                entries: list[dict[str, Any]] = []
                for index, (target, content, mode) in enumerate(self._writes):
                    staged = staged_root / str(index)
                    _write_private(staged, content, mode=mode)
                    existed = target.is_file()
                    if existed:
                        backup = backup_root / str(index)
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup)
                    entries.append(
                        {
                            "target": str(
                                target.relative_to(self.workspace.paths.root)
                            ),
                            "staged": f"staged/{index}",
                            "backup": f"backup/{index}",
                            "existed": existed,
                            "mode": mode,
                        }
                    )
            except BaseException:
                shutil.rmtree(root, ignore_errors=True)
                _cleanup_explicit_paths(self._rollback_cleanup)
                raise
            journal = {
                "version": 1,
                "transaction_id": self.transaction_id,
                "phase": "prepared",
                "entries": entries,
                "cleanup_on_rollback": [
                    str(path.relative_to(self.workspace.paths.root))
                    for path in self._rollback_cleanup
                ],
                "cleanup_on_commit": [
                    str(path.relative_to(self.workspace.paths.root))
                    for path in self._commit_cleanup
                ],
            }
            _write_journal(root, journal)
            try:
                journal["phase"] = "applying"
                _write_journal(root, journal)
                for entry in entries:
                    target = self.workspace.paths.root / str(entry["target"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _replace_file(root / str(entry["staged"]), target)
                    target.chmod(int(entry["mode"]))
                journal["phase"] = "committed"
                _write_journal(root, journal)
            except BaseException:
                _rollback(self.workspace, root, journal)
                raise
            _finish_committed(self.workspace, root, journal)


def recover_configuration_transactions(workspace: Workspace) -> tuple[str, ...]:
    """Recover interrupted transactions without reading secret file contents."""

    with _TransactionLock(workspace):
        return _recover_configuration_transactions(workspace)


def _recover_configuration_transactions(workspace: Workspace) -> tuple[str, ...]:

    root = _transaction_root(workspace)
    if not root.is_dir():
        return ()
    recovered: list[str] = []
    for transaction in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            journal = json.loads(
                (transaction / "journal.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            shutil.rmtree(transaction, ignore_errors=True)
            continue
        if not isinstance(journal, dict):
            continue
        if journal.get("phase") == "committed":
            _finish_committed(workspace, transaction, journal)
        else:
            _rollback(workspace, transaction, journal)
        recovered.append(str(journal.get("transaction_id") or transaction.name))
    return tuple(recovered)


class _TransactionLock:
    def __init__(self, workspace: Workspace) -> None:
        self.path = workspace.paths.child("state", "configuration", "transaction.lock")
        self.descriptor: int | None = None

    def __enter__(self) -> "_TransactionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self.descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                break
            except FileExistsError as error:
                if attempt == 0 and _remove_stale_lock(self.path):
                    continue
                raise RuntimeError(
                    "another Workspace configuration commit is active"
                ) from error
        assert self.descriptor is not None
        os.write(self.descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(self.descriptor)
        return self

    def __exit__(self, *_args: object) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


def _transaction_root(workspace: Workspace) -> Path:
    return workspace.paths.child("state", "configuration", "transactions")


def _write_journal(root: Path, value: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_private(
        root / "journal.json",
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o600,
    )


def _write_private(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_file(temporary, path)
        path.chmod(mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _rollback(workspace: Workspace, root: Path, journal: dict[str, Any]) -> None:
    entries = journal.get("entries", [])
    if isinstance(entries, list):
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            target = _journal_path(workspace, entry.get("target"))
            if target is None:
                continue
            backup = root / str(entry.get("backup") or "")
            if entry.get("existed") is True and backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace_file(backup, target)
            elif entry.get("existed") is not True:
                target.unlink(missing_ok=True)
    _cleanup_paths(workspace, journal.get("cleanup_on_rollback"))
    shutil.rmtree(root, ignore_errors=True)


def _finish_committed(
    workspace: Workspace, root: Path, journal: dict[str, Any]
) -> None:
    _cleanup_paths(workspace, journal.get("cleanup_on_commit"))
    shutil.rmtree(root, ignore_errors=True)


def _cleanup_paths(workspace: Workspace, values: object) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        path = _journal_path(workspace, value)
        if path is None:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _cleanup_explicit_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _journal_path(workspace: Workspace, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _workspace_path(workspace, workspace.paths.root / value)
    except ValueError:
        return None


def _workspace_path(workspace: Workspace, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace.paths.root)
    except ValueError as error:
        raise ValueError("configuration transaction path escapes Workspace") from error
    return resolved


def _replace_file(source: Path, target: Path) -> None:
    os.replace(source, target)


def _remove_stale_lock(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        path.unlink(missing_ok=True)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return True
    except PermissionError:
        return False
    return False


__all__ = [
    "WorkspaceConfigurationTransaction",
    "recover_configuration_transactions",
]
