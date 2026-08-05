"""Private persistence operations for account configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from kairospy.application.usecases.workspace.application.workspace import write_credential_file


class AccountConfigurationWriter:
    """Own account-binding file mutations behind the account application API."""

    def write_account(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_credential(self, path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_credential_file(path, payload)

    def append_credential(self, path: Path, content: str) -> None:
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n\n" + content.rstrip() + "\n", encoding="utf-8")

    def rewrite_table(self, path: Path, table: str, *, updates: Mapping[str, object], removals: set[str]) -> None:
        path.write_text(_rewrite_toml_table(path.read_text(encoding="utf-8"), table, updates=updates, removals=removals), encoding="utf-8")

    def delete_account(self, path: Path) -> None:
        path.unlink()

    def append_jsonl(self, path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def _rewrite_toml_table(text: str, table: str, *, updates: Mapping[str, object], removals: set[str]) -> str:
    lines = text.splitlines()
    header = f"[{table}]"
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        raise ValueError(f"{header} table is required")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    seen: set[str] = set()
    rewritten = list(lines[: start + 1])
    for line in lines[start + 1 : end]:
        key = _toml_assignment_key(line)
        if key is None:
            rewritten.append(line)
            continue
        if key in removals:
            seen.add(key)
            continue
        if key in updates:
            rewritten.append(f'{key} = {_toml_value(updates[key])}')
            seen.add(key)
            continue
        rewritten.append(line)
    for key, value in updates.items():
        if key not in seen:
            rewritten.append(f'{key} = {_toml_value(value)}')
    rewritten.extend(lines[end:])
    return "\n".join(rewritten).rstrip() + "\n"


def _toml_assignment_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    if not key or any(character.isspace() for character in key):
        return None
    return key


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return f'"{_toml_escape(str(value))}"'


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["AccountConfigurationWriter"]
