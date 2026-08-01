from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kairospy.surface.cli import app
from kairospy.surface.rendering.writer import render_text


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


def test_text_fallback_renders_mapping_without_json() -> None:
    output = render_text({
        "manifest": {
            "path": "/tmp/project/.kairos/kairos.toml",
            "values": {"cli": {"format": "text"}},
        },
        "workspace": {"root": "/tmp/project"},
    })

    assert output.startswith("Result\n")
    assert '"manifest"' not in output
    assert "format  text" in output


def test_workspace_text_format_controls_manifest_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    kairos = tmp_path / ".kairos"
    kairos.mkdir()
    (kairos / "kairos.toml").write_text(
        "\n".join([
            "schema_version = 1",
            "[project]",
            'name = "demo"',
            "[cli]",
            'format = "text"',
        ]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["config", "manifest"], catch_exceptions=False)

    assert result.exit_code == 0
    assert result.output.startswith("Result\n")
    assert not result.output.lstrip().startswith("{")
    assert "format  text" in result.output
