from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import shutil

from .catalog import DataCatalog
from .contracts import DatasetRelease


@dataclass(frozen=True, slots=True)
class DatasetCopyResult:
    dataset: str
    release_id: str
    source_root: str
    target_root: str
    release_path: str
    files_copied: int
    files_skipped: int
    bytes_copied: int
    source_cache_path: str | None = None
    source_cache_files_copied: int = 0
    source_cache_files_skipped: int = 0
    source_cache_bytes_copied: int = 0


def copy_dataset_release(
    source_root: str | Path,
    target_root: str | Path,
    dataset: str,
    *,
    release: str | None = None,
    include_source_cache: bool = False,
    overwrite: bool = False,
    dry_run: bool = False,
) -> DatasetCopyResult:
    source = Path(source_root).expanduser().resolve()
    target = Path(target_root).expanduser().resolve()
    if source == target:
        raise ValueError("source and target data lake roots must be different")
    source_catalog = DataCatalog(source)
    source_catalog.discover()
    source_release = source_catalog.release(release or dataset)
    if release is not None and str(source_release.product_key) != str(source_catalog.product(dataset).key):
        raise ValueError("selected release does not belong to the requested dataset")
    _validate_relative_path(source_release.relative_path)
    source_directory = source / source_release.relative_path
    target_directory = target / source_release.relative_path
    if not source_directory.exists():
        raise FileNotFoundError(source_directory)

    release_stats = _copy_tree(source_directory, target_directory, overwrite=overwrite, dry_run=dry_run)
    source_cache_stats = _CopyStats()
    source_cache_path = None
    if include_source_cache and source_release.provider:
        relative_source_cache = Path("source") / f"provider={source_release.provider}"
        source_cache = source / relative_source_cache
        if source_cache.exists():
            source_cache_path = relative_source_cache.as_posix()
            source_cache_stats = _copy_tree(
                source_cache, target / relative_source_cache, overwrite=overwrite, dry_run=dry_run,
            )

    if not dry_run:
        target_catalog = DataCatalog(target)
        try:
            target_catalog.register_product_spec(source_catalog.product_spec(source_release.product_key), enrich=True)
        except KeyError:
            target_catalog.register_product(source_catalog.product(source_release.product_key), enrich=True)
        target_catalog.register_release(source_release)
        target_catalog.save()

    return DatasetCopyResult(
        dataset=str(source_release.product_key),
        release_id=source_release.release_id,
        source_root=str(source),
        target_root=str(target),
        release_path=source_release.relative_path,
        files_copied=release_stats.files_copied,
        files_skipped=release_stats.files_skipped,
        bytes_copied=release_stats.bytes_copied,
        source_cache_path=source_cache_path,
        source_cache_files_copied=source_cache_stats.files_copied,
        source_cache_files_skipped=source_cache_stats.files_skipped,
        source_cache_bytes_copied=source_cache_stats.bytes_copied,
    )


@dataclass(slots=True)
class _CopyStats:
    files_copied: int = 0
    files_skipped: int = 0
    bytes_copied: int = 0


def _copy_tree(source: Path, target: Path, *, overwrite: bool, dry_run: bool) -> _CopyStats:
    stats = _CopyStats()
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        if destination.exists() and not overwrite:
            if not _same_file(path, destination):
                raise FileExistsError(f"target file exists with different content: {destination}")
            stats.files_skipped += 1
            continue
        size = path.stat().st_size
        stats.files_copied += 1
        stats.bytes_copied += size
        if dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return stats


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"release path must be lake-relative: {value}")


def _same_file(first: Path, second: Path) -> bool:
    first_stat = first.stat()
    second_stat = second.stat()
    if first_stat.st_size != second_stat.st_size:
        return False
    return _sha256(first) == _sha256(second)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
