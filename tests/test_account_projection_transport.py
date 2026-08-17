from __future__ import annotations

import struct
from pathlib import Path

import flatbuffers

from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.account import AccountCurrentViewReader
from kairospy.infrastructure.transport.native import native
from kairospy.infrastructure.transport.generated.kairos.account.v2 import (
    AccountCurrentView,
    AccountModel,
    AccountSegmentState,
    AccountStatus,
    FreshnessState,
)
from kairospy.infrastructure.transport.generated.kairos.common.v2 import (
    ViewCompleteness,
    ViewMetadata,
)


def _account_snapshot(*, generation: int = 7, completeness: int = 1) -> bytes:
    builder = flatbuffers.Builder(2048)

    def account(segment: str) -> int:
        segment_key = builder.CreateString(segment)
        environment = builder.CreateString("live")
        broker = builder.CreateString("binance")
        empty_balances = builder.StartVector(4, 0, 4)
        balances = builder.EndVector()
        empty_collateral = builder.StartVector(4, 0, 4)
        collateral = builder.EndVector()
        empty_positions = builder.StartVector(4, 0, 4)
        positions = builder.EndVector()
        AccountSegmentState.AccountSegmentStateStart(builder)
        AccountSegmentState.AccountSegmentStateAddSegmentKey(builder, segment_key)
        AccountSegmentState.AccountSegmentStateAddEnvironment(builder, environment)
        AccountSegmentState.AccountSegmentStateAddBroker(builder, broker)
        AccountSegmentState.AccountSegmentStateAddObservedAccountModel(builder, AccountModel.AccountModel.MARGIN)
        AccountSegmentState.AccountSegmentStateAddStatus(builder, AccountStatus.AccountStatus.ACTIVE)
        AccountSegmentState.AccountSegmentStateAddFreshness(builder, FreshnessState.FreshnessState.FRESH)
        AccountSegmentState.AccountSegmentStateAddObservedAtUnixNanos(builder, 1_000)
        AccountSegmentState.AccountSegmentStateAddStateGeneration(builder, 7)
        AccountSegmentState.AccountSegmentStateAddBalances(builder, balances)
        AccountSegmentState.AccountSegmentStateAddCollateral(builder, collateral)
        AccountSegmentState.AccountSegmentStateAddPositions(builder, positions)
        return AccountSegmentState.AccountSegmentStateEnd(builder)

    rows = (account("spot"), account("usd_m_futures"))
    AccountCurrentView.AccountCurrentViewStartSegmentsVector(builder, len(rows))
    for row in reversed(rows):
        builder.PrependUOffsetTRelative(row)
    row_vector = builder.EndVector()
    account_id = builder.CreateString("main")
    snapshot_id = builder.CreateString("account:main:7")
    resource_id = builder.CreateString("account:main")
    view_key = builder.CreateString("runtime=account:main;account=main;view=current")
    owner = builder.CreateString("account:main")
    ViewMetadata.ViewMetadataStart(builder)
    ViewMetadata.ViewMetadataAddSnapshotId(builder, snapshot_id)
    ViewMetadata.ViewMetadataAddResourceId(builder, resource_id)
    ViewMetadata.ViewMetadataAddViewKey(builder, view_key)
    ViewMetadata.ViewMetadataAddOwnerId(builder, owner)
    ViewMetadata.ViewMetadataAddGeneration(builder, generation)
    ViewMetadata.ViewMetadataAddAsOfUnixNanos(builder, 1_000)
    ViewMetadata.ViewMetadataAddPublishedAtUnixNanos(builder, 2_000)
    ViewMetadata.ViewMetadataAddCompleteness(builder, completeness)
    ViewMetadata.ViewMetadataAddAppliedRevision(builder, 11)
    metadata = ViewMetadata.ViewMetadataEnd(builder)
    AccountCurrentView.AccountCurrentViewStart(builder)
    AccountCurrentView.AccountCurrentViewAddMetadata(builder, metadata)
    AccountCurrentView.AccountCurrentViewAddAccountId(builder, account_id)
    AccountCurrentView.AccountCurrentViewAddSegments(builder, row_vector)
    root = AccountCurrentView.AccountCurrentViewEnd(builder)
    builder.Finish(root, file_identifier=b"AAV2")
    return bytes(builder.Output())


def _write_shared_snapshot(path: Path, payload: bytes) -> None:
    slot_size = 4096
    data = bytearray(64 + 2 * slot_size)
    data[:4] = b"KSS1"
    struct.pack_into("<HHI", data, 4, 1, 2, slot_size)
    data[64 : 64 + len(payload)] = payload
    struct.pack_into("<I", data, 24, len(payload))
    struct.pack_into("<Q", data, 32, 7)
    path.write_bytes(data)


def test_one_account_mmap_decodes_every_segment_at_one_generation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "account-main.snapshot"
    _write_shared_snapshot(path, _account_snapshot())

    snapshot = AccountCurrentViewReader(path, account_id=AccountId("main")).snapshot(AccountId("main"))

    assert snapshot.generation == 7
    assert [str(value.segment_key) for value in snapshot.segments] == [
        "spot",
        "usd_m_futures",
    ]
    assert {value.generation for value in snapshot.segments} == {7}
    assert snapshot.event_sequence == 11


def test_account_projection_rejects_frame_metadata_generation_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "account-main.snapshot"
    _write_shared_snapshot(path, _account_snapshot(generation=8))

    try:
        AccountCurrentViewReader(path, account_id=AccountId("main")).snapshot(AccountId("main"))
    except ValueError as error:
        assert "generation disagree" in str(error)
    else:
        raise AssertionError("generation mismatch must fail closed")


def test_account_projection_rejects_incomplete_or_corrupt_mmap(tmp_path: Path) -> None:
    path = tmp_path / "account-main.snapshot"
    _write_shared_snapshot(path, _account_snapshot(completeness=2))
    try:
        AccountCurrentViewReader(path, account_id=AccountId("main")).snapshot(AccountId("main"))
    except ValueError as error:
        assert "not complete" in str(error)
    else:
        raise AssertionError("partial Account view must fail closed")

    path.write_bytes(b"not-a-shared-snapshot")
    try:
        AccountCurrentViewReader(path, account_id=AccountId("main")).snapshot(AccountId("main"))
    except native.CorruptSnapshotError as error:
        assert error.code == "corrupt_snapshot"
    else:
        raise AssertionError("corrupt Account mmap must fail closed")


def test_account_projection_reopens_after_publisher_restart(tmp_path: Path) -> None:
    path = tmp_path / "account-main.snapshot"
    _write_shared_snapshot(path, _account_snapshot())
    first = AccountCurrentViewReader(path, account_id=AccountId("main")).snapshot(AccountId("main"))
    path.unlink()
    _write_shared_snapshot(path, _account_snapshot())
    second = AccountCurrentViewReader(path, account_id=AccountId("main")).snapshot(AccountId("main"))
    assert second == first
