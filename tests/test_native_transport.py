from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path

import pytest

from kairospy.infrastructure.transport import native as native_facade
from kairospy.infrastructure.protocol.generated_spec import TRANSPORT_FINGERPRINT
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader


FIXTURE = Path(__file__).parent / "fixtures" / "kss2_snapshot.bin"


def test_build_info_matches_transport_only_fingerprint() -> None:
    build = native_facade.native.build_info()
    assert build.api_version == 1
    assert build.transport_envelope_versions == (1, 2)
    assert build.transport_fingerprint == TRANSPORT_FINGERPRINT


@dataclass
class _Build:
    api_version: int = 1
    transport_fingerprint: str = TRANSPORT_FINGERPRINT
    package_version: str = "test"


def test_build_info_mismatch_fails_fast() -> None:
    with pytest.raises(ImportError, match="API mismatch"):
        native_facade._validate_build_info(_Build(api_version=2))
    with pytest.raises(ImportError, match="fingerprint mismatch"):
        native_facade._validate_build_info(_Build(transport_fingerprint="wrong"))


def test_native_snapshot_returns_transport_metadata_and_owned_bytes() -> None:
    with SharedSnapshotReader(FIXTURE) as reader:
        frame = reader.read()
    assert frame.envelope_version == 2
    assert frame.resource_epoch == 2
    assert frame.producer_incarnation == 3
    assert frame.generation == 7
    assert frame.applied_event_sequence == 11
    assert frame.published_at_unix_nanos == 2_000
    assert frame.payload == b"cross-language-payload"


def test_reader_is_thread_safe_and_close_is_idempotent() -> None:
    reader = SharedSnapshotReader(FIXTURE)
    with ThreadPoolExecutor(max_workers=8) as executor:
        payloads = list(executor.map(lambda _: reader.read().payload, range(64)))
    assert set(payloads) == {b"cross-language-payload"}
    reader.close()
    reader.close()
    with pytest.raises(native_facade.native.ClosedError) as raised:
        reader.read()
    assert raised.value.code == "closed"


def test_checksum_corruption_has_stable_exception_code(tmp_path: Path) -> None:
    corrupted = tmp_path / "corrupt.bin"
    data = bytearray(FIXTURE.read_bytes())
    offset = data.index(b"cross-language-payload")
    data[offset] ^= 0xFF
    corrupted.write_bytes(data)
    reader = SharedSnapshotReader(corrupted)
    with pytest.raises(native_facade.native.CorruptSnapshotError) as raised:
        reader.read()
    assert raised.value.code == "corrupt_snapshot"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is Unix-only")
def test_reader_rejects_use_after_fork() -> None:
    reader = SharedSnapshotReader(FIXTURE)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            reader.read()
        except Exception as error:
            os.write(write_fd, getattr(error, "code", "missing").encode())
        finally:
            os._exit(0)
    os.close(write_fd)
    code = os.read(read_fd, 128).decode()
    os.waitpid(pid, 0)
    reader.close()
    assert code == "forked_process"
