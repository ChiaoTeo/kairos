from __future__ import annotations

import asyncio

from kairospy.surface.interactive.line_reader import (
    _FilteredFileHistory,
    clear_history,
    default_history_path,
    load_history_entries,
    should_record_history,
)
from kairospy.surface.interactive.shell import AppSession


class FakeLineReader:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.prompts: list[str] = []

    def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


def test_history_path_uses_xdg_state_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert default_history_path() == tmp_path / "kairospy" / "shell_history"


def test_history_filter_skips_blank_repeated_and_sensitive_lines() -> None:
    assert should_record_history("account query balance main")
    assert not should_record_history("")
    assert not should_record_history("   ")
    assert not should_record_history("account query balance main", previous="account query balance main")
    assert not should_record_history("credential set okx --api-key abc")
    assert not should_record_history("launch --token abc")
    assert not should_record_history("account create --password abc")


def test_filtered_history_persists_only_allowed_entries(tmp_path) -> None:
    path = tmp_path / "history"
    history = _FilteredFileHistory(path, max_history=1000)

    history.append_string("")
    history.append_string("account query balance main")
    history.append_string("account query balance main")
    history.append_string("credential set okx --api-key abc")
    history.append_string("market quote BTC/USDT")

    text = path.read_text(encoding="utf-8")
    assert "+account query balance main" in text
    assert "+market quote BTC/USDT" in text
    assert "api-key" not in text
    assert text.count("+account query balance main") == 1


def test_load_history_entries_returns_chronological_limited_entries(tmp_path) -> None:
    path = tmp_path / "history"
    history = _FilteredFileHistory(path, max_history=1000)
    history.append_string("one")
    history.append_string("two")
    history.append_string("three")

    assert load_history_entries(history_path=path) == ["one", "two", "three"]
    assert load_history_entries(history_path=path, limit=2) == ["two", "three"]


def test_clear_history_removes_history_file(tmp_path) -> None:
    path = tmp_path / "history"
    history = _FilteredFileHistory(path, max_history=1000)
    history.append_string("one")

    clear_history(history_path=path)

    assert not path.exists()


def test_filtered_history_clear_removes_file_and_memory(tmp_path) -> None:
    path = tmp_path / "history"
    history = _FilteredFileHistory(path, max_history=1000)
    history.append_string("one")

    history.clear()

    assert not path.exists()
    assert history.get_strings() == []


def test_filtered_history_loads_prompt_toolkit_async_interface(tmp_path) -> None:
    path = tmp_path / "history"
    history = _FilteredFileHistory(path, max_history=1000)
    history.append_string("account query balance main")
    history.append_string("market quote BTC/USDT")

    loaded = asyncio.run(_load_history(history))

    assert loaded == ["market quote BTC/USDT", "account query balance main"]


def test_filtered_history_prunes_old_entries(tmp_path) -> None:
    path = tmp_path / "history"
    history = _FilteredFileHistory(path, max_history=2)

    history.append_string("one")
    history.append_string("two")
    history.append_string("three")

    text = path.read_text(encoding="utf-8")
    assert "+one" not in text
    assert "+two" in text
    assert "+three" in text


def test_app_session_run_uses_injected_line_reader() -> None:
    reader = FakeLineReader(["account", "quit"])
    session = AppSession(line_reader=reader)

    session.run()

    assert reader.prompts == ["kairos/app> ", "kairos/app/account> "]


async def _load_history(history: _FilteredFileHistory) -> list[str]:
    return [item async for item in history.load()]
