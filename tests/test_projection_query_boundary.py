from __future__ import annotations

from pathlib import Path

from kairospy.application.support.query.projections.service import LaunchProjectionService


class FakeProjectionReader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def exists(self, name: str) -> bool:
        return name == "summary.json"

    def read_json(self, name: str) -> dict[str, object]:
        return {"launch_id": "demo"} if name == "summary.json" else {}

    def read_jsonl(self, name: str) -> list[dict[str, object]]:
        return []


def test_projection_query_consumes_support_owned_reader_protocol(tmp_path: Path) -> None:
    service = LaunchProjectionService(FakeProjectionReader(tmp_path))

    assert service.load("run.summary") == {"launch_id": "demo"}


def test_projection_query_can_be_composed_with_filesystem_reader(tmp_path: Path) -> None:
    from kairospy.application.support.composition.application.projections import launch_projection_query
    from kairospy.infrastructure.persistence.services.artifacts.run_sqlite import RunSqliteStore

    RunSqliteStore(tmp_path / "run.sqlite").write_json("summary", {"launch_id": "demo"})

    assert launch_projection_query(tmp_path).load("run.summary") == {"launch_id": "demo"}


def test_projection_instance_discovery_is_composed_from_infrastructure(tmp_path: Path) -> None:
    from kairospy.application.support.composition.application.projections import (
        find_latest_instance,
        list_instances,
    )
    from kairospy.infrastructure.persistence.services.artifacts.run_sqlite import RunSqliteStore

    instance = tmp_path / "paper" / "demo" / "instances" / "one"
    instance.mkdir(parents=True)
    store = RunSqliteStore(instance / "run.sqlite")
    store.write_json("summary", {"launch_id": "demo", "mode": "paper"})
    store.append_record("records", {"time": "2026-01-01T00:00:00Z"})

    assert find_latest_instance(tmp_path, mode="paper", launch_id="demo") == instance
    assert list_instances(tmp_path, mode="paper", launch_id="demo")[0]["directory"] == str(instance)
