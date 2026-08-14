from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_v2_schema_boundary_and_registry_are_valid() -> None:
    result = subprocess.run(
        ["python3", "scripts/generate/validate_v2_schemas.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("v2 schema validation passed (")
    assert result.stdout.rstrip().endswith(" FlatBuffers roots)")
