from __future__ import annotations

from pathlib import Path
import subprocess

from kairospy.infrastructure.transport.indexed_view import (
    IndexedViewReader,
    IndexedViewSchema,
)


def test_rust_writer_is_readable_by_native_python_reader(tmp_path: Path) -> None:
    environment = tmp_path / "current.lmdb"
    subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "-p",
            "kairos-indexed-view",
            "--example",
            "write_fixture",
            "--",
            str(environment),
        ],
        check=True,
    )
    schemas = (
        IndexedViewSchema("orders", 1, "EO03", 1),
        IndexedViewSchema("intents", 1, "EI03", 1),
    )
    with IndexedViewReader(
        environment,
        map_size=8 * 1024 * 1024,
        workspace_id="workspace",
        launch_id="launch",
        instance_id="instance",
        owner="Execution",
        publisher_resource_id="execution-main",
        resource_epoch=1,
        schemas=schemas,
    ) as reader:
        assert reader.get("orders", b"order/1") == b"open"
        assert reader.get("orders", b"missing") is None
        value_metadata, value = reader.value_snapshot("orders", b"order/1")
        assert value == b"open"
        assert value_metadata.applied_event_sequence == 11
        assert reader.prefix("orders", b"order/", limit=1) == (
            (b"order/1", b"open"),
        )
        snapshot = reader.snapshot(
            (
                ("orders", b"order/", 10),
                ("intents", b"intent/", 10),
            )
        )
        assert snapshot.rows["orders"] == (
            (b"order/1", b"open"),
            (b"order/2", b"pending"),
        )
        assert snapshot.rows["intents"] == ((b"intent/1", b"active"),)
        assert snapshot.metadata.applied_event_sequence == 11
        metadata = reader.metadata()
        assert metadata.format_version == 3
        assert metadata.resource_epoch == 1
        assert metadata.producer_incarnation == 3
        assert metadata.applied_event_sequence == 11
        assert metadata.committed_at_unix_nanos == 2_000
        assert metadata.rebuild_state == "ready"
