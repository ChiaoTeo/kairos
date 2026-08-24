"""Project-scoped named Dataset Set descriptions and movable aliases."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .catalog import DatasetCatalogApplication
from .models import DatasetSetRef


_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class DatasetSetRegistryApplication:
    """Persist immutable compositions while allowing an explicit alias to move."""

    catalog: DatasetCatalogApplication

    @property
    def path(self) -> Path:
        return self.catalog.workspace.paths.child("state", "data", "dataset-sets.json")

    def pin(
        self,
        name: str,
        dataset_set: DatasetSetRef,
        *,
        expected_current_hash: str | None = None,
    ) -> DatasetSetRef:
        alias = self._name(name)
        for member in dataset_set.members:
            if self.catalog.inspect(member.dataset_id, member.version) != member:
                raise ValueError(
                    f"Dataset Set member does not match Catalog: {member.identity}"
                )
        lock_path = self.path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                value = self._read()
                aliases = value.setdefault("aliases", {})
                compositions = value.setdefault("compositions", {})
                current = aliases.get(alias)
                if (
                    expected_current_hash is not None
                    and current != expected_current_hash
                ):
                    raise ValueError(
                        f"Dataset Set alias changed: expected={expected_current_hash}, "
                        f"actual={current}"
                    )
                compositions.setdefault(
                    dataset_set.composition_hash, dataset_set.as_dict()
                )
                aliases[alias] = dataset_set.composition_hash
                self._write(value)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return dataset_set

    def load(self, name: str, *, composition_hash: str | None = None) -> DatasetSetRef:
        alias = self._name(name)
        value = self._read()
        selected = composition_hash or value.get("aliases", {}).get(alias)
        if not isinstance(selected, str):
            raise FileNotFoundError(f"Dataset Set alias does not exist: {alias}")
        raw = value.get("compositions", {}).get(selected)
        if not isinstance(raw, Mapping):
            raise FileNotFoundError(
                f"Dataset Set composition does not exist: {selected}"
            )
        dataset_set = DatasetSetRef.from_dict(raw)
        if dataset_set.composition_hash != selected:
            raise ValueError("Dataset Set registry composition identity is invalid")
        for member in dataset_set.members:
            if self.catalog.inspect(member.dataset_id, member.version) != member:
                raise ValueError(
                    f"Dataset Set member no longer matches Catalog: {member.identity}"
                )
        return dataset_set

    def aliases(self) -> Mapping[str, str]:
        return dict(self._read().get("aliases", {}))

    @staticmethod
    def _name(value: str) -> str:
        normalized = value.strip()
        if not _NAME.fullmatch(normalized):
            raise ValueError("Dataset Set alias must be a safe non-empty name")
        return normalized

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": 1, "aliases": {}, "compositions": {}}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("Dataset Set registry is invalid")
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.path)


__all__ = ["DatasetSetRegistryApplication"]
