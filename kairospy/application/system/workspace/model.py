from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kairospy.config import DEFAULT_DATA_ROOT, DEFAULT_REFERENCE_ROOT, KairosConfig, find_manifest_path, load_config

from .accounts import AccountStore
from .operations import OperationJournal
from .run_index import RunIndex


DEFAULT_WORKSPACE_ROOT = ".kairos"
DEFAULT_RUN_ROOT = ".kairos/runs"


@dataclass(frozen=True, slots=True)
class KairosWorkspace:
    root: Path
    manifest_path: Path | None
    manifest: KairosConfig
    workspace_root: Path
    state_root: Path
    run_root: Path
    data_root: Path
    reference_root: Path
    accounts_root: Path
    run_index_path: Path
    operations_path: Path
    accounts: AccountStore
    run_index: RunIndex
    operations: OperationJournal

    @classmethod
    def resolve(cls, start: str | Path | None = None) -> "KairosWorkspace":
        manifest_path = find_manifest_path(start)
        config = load_config(manifest_path)
        root = config.root.resolve()
        workspace_root = _path(config, "paths", "workspace_root", DEFAULT_WORKSPACE_ROOT)
        state_root = workspace_root / "state"
        run_root = _path(config, "paths", "run_root", DEFAULT_RUN_ROOT)
        data_root = _path(config, "paths", "lake_root", DEFAULT_DATA_ROOT)
        reference_root = _path(config, "paths", "reference_root", DEFAULT_REFERENCE_ROOT)
        accounts_root = _path(config, "paths", "accounts_root", str(workspace_root / "accounts"))
        run_index_path = _path(config, "paths", "run_index", str(state_root / "run-index.json"))
        operations_path = _path(config, "paths", "operations", str(state_root / "operations.jsonl"))
        account_store = AccountStore.load(accounts_root)
        run_index = RunIndex(run_index_path, root=root)
        operations = OperationJournal(operations_path)
        return cls(
            root=root,
            manifest_path=manifest_path,
            manifest=config,
            workspace_root=workspace_root,
            state_root=state_root,
            run_root=run_root,
            data_root=data_root,
            reference_root=reference_root,
            accounts_root=accounts_root,
            run_index_path=run_index_path,
            operations_path=operations_path,
            accounts=account_store,
            run_index=run_index,
            operations=operations,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "manifest_path": str(self.manifest_path) if self.manifest_path is not None else None,
            "workspace_root": str(self.workspace_root),
            "state_root": str(self.state_root),
            "run_root": str(self.run_root),
            "data_root": str(self.data_root),
            "reference_root": str(self.reference_root),
            "accounts_root": str(self.accounts_root),
            "run_index_path": str(self.run_index_path),
            "operations_path": str(self.operations_path),
        }

    @property
    def project_config_path(self) -> Path | None:
        return self.manifest_path

    @property
    def project_config(self) -> KairosConfig:
        return self.manifest


def _path(config: KairosConfig, section: str, key: str, default: str) -> Path:
    raw = _section_value(config.values, section, key)
    value = raw if isinstance(raw, str) and raw.strip() else default
    return config.resolve_path(value)


def _section_value(values: Mapping[str, Any], section: str, key: str) -> object:
    table = values.get(section)
    if not isinstance(table, Mapping):
        return None
    return table.get(key)
