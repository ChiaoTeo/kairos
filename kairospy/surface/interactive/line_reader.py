from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class LineReader(Protocol):
    def read(self, prompt: str) -> str:
        ...


class BasicLineReader:
    def read(self, prompt: str) -> str:
        return input(prompt)


class PromptToolkitLineReader:
    def __init__(self, *, history_path: Path | None = None, max_history: int = 1000) -> None:
        from prompt_toolkit import PromptSession

        self._history = _FilteredFileHistory(history_path or default_history_path(), max_history=max_history)
        self._session = PromptSession(history=self._history)

    def read(self, prompt: str) -> str:
        return self._session.prompt(prompt)

    def clear_history(self) -> None:
        self._history.clear()


def default_line_reader() -> LineReader:
    try:
        return PromptToolkitLineReader()
    except Exception:
        return BasicLineReader()


def default_history_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "kairospy" / "shell_history"
    return Path.home() / ".local" / "state" / "kairospy" / "shell_history"


def load_history_entries(*, history_path: Path | None = None, limit: int | None = None) -> list[str]:
    from prompt_toolkit.history import FileHistory

    path = history_path or default_history_path()
    if not path.exists():
        return []
    entries = list(reversed(list(FileHistory(str(path)).load_history_strings())))
    if limit is None:
        return entries
    return entries[-limit:]


def clear_history(*, history_path: Path | None = None) -> None:
    path = history_path or default_history_path()
    if path.exists():
        path.unlink()


def should_record_history(line: str, *, previous: str | None = None) -> bool:
    command = line.strip()
    if not command:
        return False
    if previous is not None and command == previous.strip():
        return False
    lowered = command.lower()
    sensitive_terms = (
        "password",
        "passwd",
        "secret",
        "token",
        "api-key",
        "apikey",
        "private-key",
        "private_key",
        "credential set",
    )
    return not any(term in lowered for term in sensitive_terms)


class _FilteredFileHistory:
    def __init__(self, filename: Path, *, max_history: int = 1000) -> None:
        from prompt_toolkit.history import FileHistory

        self.filename = filename
        self.max_history = max_history
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        self._inner = FileHistory(str(filename))
        self._last_recorded = _last_history_entry(filename)

    def load_history_strings(self):
        return self._inner.load_history_strings()

    async def load(self):
        async for item in self._inner.load():
            yield item

    def get_strings(self):
        return self._inner.get_strings()

    def append_string(self, string: str) -> None:
        if not should_record_history(string, previous=self._last_recorded):
            return
        self._inner.append_string(string.strip())
        self._last_recorded = string.strip()
        _prune_history_file(self.filename, max_entries=self.max_history)

    def store_string(self, string: str) -> None:
        self.append_string(string)

    def clear(self) -> None:
        clear_history(history_path=self.filename)
        self._last_recorded = None
        self._inner._loaded = True
        self._inner._loaded_strings = []


def _last_history_entry(path: Path) -> str | None:
    if not path.exists():
        return None
    last: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("+"):
            last = line[1:]
    return last


def _prune_history_file(path: Path, *, max_entries: int) -> None:
    if max_entries <= 0 or not path.exists():
        return
    blocks = [block for block in path.read_text(encoding="utf-8").split("\n\n") if block.strip()]
    if len(blocks) <= max_entries:
        return
    path.write_text("\n\n".join(blocks[-max_entries:]) + "\n", encoding="utf-8")
