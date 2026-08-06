from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.usecases.workspace.application.project import ProjectAdminApplication


def test_project_init_creates_kairos_workspace(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"

    result = ProjectAdminApplication().init(str(project_root))

    assert Path(result).resolve() == project_root.resolve()
    assert (project_root / ".kairos" / "kairos.toml").read_text(encoding="utf-8") == (
        "schema_version = 1\n\n"
        "[project]\n"
        'name = "demo"\n'
        'timezone = "UTC"\n'
        'language = "en"\n\n'
        "[data]\n"
        'storage_format = "parquet"\n\n'
        "[cli]\n"
        'format = "text"\n'
        "launch_control = true\n"
    )
    for directory in ("accounts", "state", "launches", "data", "reference", "orders/journals"):
        assert (project_root / ".kairos" / directory).is_dir()


def test_project_init_rejects_existing_manifest_without_force(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    application = ProjectAdminApplication()
    application.init(str(project_root))

    with pytest.raises(ValueError, match="already exists"):
        application.init(str(project_root))
