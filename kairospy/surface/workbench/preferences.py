"""Project-scoped preferences for the Workbench surface."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tomllib
from uuid import uuid4

from .theme import resolve_theme_name, theme_alias


_SECTION_PATTERN = re.compile(r"(?m)^[ \t]*\[([^\]\n]+)\][ \t]*(?:#.*)?$")
_THEME_PATTERN = re.compile(r"(?m)^[ \t]*theme[ \t]*=.*$")
_JOINED_WORKBENCH_PATTERN = re.compile(
    r"(?m)^[ \t]*\[workbench\][ \t]*theme[ \t]*=.*$"
)


def load_project_theme(owner: object | None) -> str | None:
    """Read an optional ``[workbench].theme`` from the project manifest."""

    manifest = _manifest_path(owner)
    if manifest is None:
        return None
    try:
        values = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    workbench = values.get("workbench")
    if not isinstance(workbench, dict):
        return None
    value = workbench.get("theme")
    return resolve_theme_name(value) if isinstance(value, str) else None


def save_project_theme(owner: object | None, registered_name: str) -> bool:
    """Persist a curated theme while preserving unrelated TOML content."""

    manifest = _manifest_path(owner)
    alias = theme_alias(registered_name)
    if manifest is None or alias is None:
        return False
    try:
        source = manifest.read_text(encoding="utf-8")
        updated = _set_workbench_theme(source, alias)
        if updated == source:
            return True
        temporary = manifest.with_name(f".{manifest.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(updated, encoding="utf-8")
            temporary.chmod(stat.S_IMODE(manifest.stat().st_mode))
            os.replace(temporary, manifest)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _manifest_path(owner: object | None) -> Path | None:
    paths = getattr(owner, "paths", None)
    explicit = getattr(paths, "manifest", None)
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_file() else None
    root_value = getattr(paths, "root", None)
    if root_value is None:
        return None
    root = Path(root_value)
    for name in ("kairos.toml", "workspace.toml"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _set_workbench_theme(source: str, alias: str) -> str:
    joined = _JOINED_WORKBENCH_PATTERN.search(source)
    if joined is not None:
        repaired = _JOINED_WORKBENCH_PATTERN.sub(
            f'[workbench]\ntheme = "{alias}"', source, count=1
        )
        return repaired.rstrip("\n") + "\n"
    sections = list(_SECTION_PATTERN.finditer(source))
    for index, section in enumerate(sections):
        if section.group(1).strip() != "workbench":
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(source)
        body = source[section.end() : end]
        replacement = f'theme = "{alias}"'
        if _THEME_PATTERN.search(body):
            body = _THEME_PATTERN.sub(replacement, body, count=1)
        else:
            body = f"\n{replacement}" + body
        updated = source[: section.end()] + body + source[end:]
        return updated.rstrip("\n") + "\n"
    prefix = source.rstrip("\n")
    separator = "\n\n" if prefix else ""
    return prefix + separator + f'[workbench]\ntheme = "{alias}"\n'


__all__ = ["load_project_theme", "save_project_theme"]
