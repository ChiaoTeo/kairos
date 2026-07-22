from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from shutil import copyfile
from typing import Mapping


_METADATA_FILES = {"manifest.json", "quality.json", "schema.json", "lineage.json"}


@dataclass(frozen=True, slots=True)
class SourceCacheEntry:
    name: str
    relative_path: str
    size_bytes: int
    content_hash: str

    def to_primitive(self) -> dict[str, object]:
        return asdict(self)


class SourceCacheStore:
    """Private source cache for user and provider Data evidence.

    Users interact with Dataset names. This store owns the internal file copy
    that audit/replay can use without depending on a user's original path.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def release_directory(self, dataset_id: str, release_id: str) -> Path:
        return self.root / "external" / dataset_id.replace(".", "/") / release_id

    def cache_user_file(self, source: str | Path, *, dataset_id: str, release_id: str) -> SourceCacheEntry:
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        if not source_path.is_file():
            raise ValueError(f"source cache expects a file: {source_path}")
        directory = self.release_directory(dataset_id, release_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / source_path.name
        if not target.exists() or target.read_bytes() != source_path.read_bytes():
            copyfile(source_path, target)
        return self.entry_for(directory, target)

    def summary(
        self,
        release_directory: str | Path,
        *,
        provider: str | None = None,
        venue: str | None = None,
        source: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        directory = Path(release_directory)
        entries = [
            self.entry_for(directory, path)
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name not in _METADATA_FILES
        ]
        return {
            "provider": provider,
            "venue": venue,
            "source": dict(source or {}),
            "stored_files": [entry.relative_path for entry in entries],
            "files": [entry.to_primitive() for entry in entries],
        }

    @staticmethod
    def entry_for(directory: str | Path, path: str | Path) -> SourceCacheEntry:
        base = Path(directory)
        value = Path(path)
        data = value.read_bytes()
        return SourceCacheEntry(
            name=value.name,
            relative_path=str(value.relative_to(base)),
            size_bytes=len(data),
            content_hash=sha256(data).hexdigest(),
        )
