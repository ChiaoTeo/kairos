from io import StringIO

from typer.testing import CliRunner

from kairospy.surface.cli.commands.reference import reference_app


def test_reference_asset_browse_is_available_and_pages(tmp_path, monkeypatch) -> None:
    root = tmp_path / "reference"
    monkeypatch.setattr("sys.stdin", StringIO("q\n"))
    result = CliRunner().invoke(reference_app, ["assets", "browse", "--root", str(root), "--page-size", "1"])

    assert result.exit_code == 0
    assert "page 1/1" in result.output
    assert "browse>" in result.output
