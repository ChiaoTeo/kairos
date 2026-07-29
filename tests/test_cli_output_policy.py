from __future__ import annotations

from pathlib import Path


COMMAND_ROOT = Path(__file__).resolve().parents[1] / "kairospy" / "surface" / "cli" / "commands"


def test_command_modules_do_not_define_fixed_json_echo_helpers() -> None:
    offenders = []
    for path in COMMAND_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "def _echo(" in text:
            offenders.append(path.name)

    assert offenders == []


def test_reference_commands_use_shared_output_resolution() -> None:
    text = (COMMAND_ROOT / "reference.py").read_text(encoding="utf-8")

    assert "workspace_cli_format" not in text
    assert "resolve_output" in text
