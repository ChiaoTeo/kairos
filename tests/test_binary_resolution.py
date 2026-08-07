from pathlib import Path

from kairospy.application.system.binaries import resolve_binary


def test_binary_resolver_honors_explicit_override(tmp_path: Path) -> None:
    binary = tmp_path / "reference-server"
    binary.write_text("binary", encoding="utf-8")
    assert resolve_binary("kairos-reference-server", override=str(binary)) == str(binary)


def test_binary_resolver_finds_development_target() -> None:
    value = resolve_binary("kairos-reference-server")
    assert value.endswith("kairos-reference-server")
