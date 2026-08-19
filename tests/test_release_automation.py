from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release" / "release_plan.py"
SPEC = importlib.util.spec_from_file_location("release_plan", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_plan = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_plan
SPEC.loader.exec_module(release_plan)


def test_release_metadata_is_consistent() -> None:
    metadata = release_plan.load_release_metadata(ROOT)

    assert metadata.name == "kairospy"
    assert metadata.version == "0.2.0"
    assert "kairos-capital" in metadata.workspace_packages


def test_unpublished_version_is_selected_for_release() -> None:
    metadata = release_plan.ReleaseMetadata("kairospy", "0.2.0", ())

    plan = release_plan.create_plan(
        metadata,
        current_pypi_version="0.1.0",
        check_git=False,
    )

    assert plan.publish is True
    assert plan.metadata.tag == "v0.2.0"


def test_published_version_is_not_republished() -> None:
    metadata = release_plan.ReleaseMetadata("kairospy", "0.2.0", ())

    plan = release_plan.create_plan(
        metadata,
        current_pypi_version="0.2.0",
        check_git=False,
    )

    assert plan.publish is False


def test_older_version_is_rejected() -> None:
    metadata = release_plan.ReleaseMetadata("kairospy", "0.1.0", ())

    try:
        release_plan.create_plan(
            metadata,
            current_pypi_version="0.2.0",
            check_git=False,
        )
    except release_plan.ReleasePlanError as error:
        assert "older than PyPI" in str(error)
    else:
        raise AssertionError("an older release version must be rejected")
