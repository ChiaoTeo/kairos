from __future__ import annotations

import struct
from pathlib import Path

import flatbuffers

from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.account import AccountMmapProjection
from kairospy.infrastructure.transport.generated.kairos.account.v1 import (
    Account,
    Accounts,
    AccountsSnapshot,
)
from kairospy.infrastructure.transport.generated.kairos.common.v1 import SnapshotHeader


def _account_snapshot() -> bytes:
    builder = flatbuffers.Builder(2048)

    def account(segment: str) -> int:
        account_id = builder.CreateString("main")
        segment_key = builder.CreateString(segment)
        environment = builder.CreateString("live")
        broker = builder.CreateString("binance")
        status = builder.CreateString("ready")
        Account.AccountStart(builder)
        Account.AccountAddAccountId(builder, account_id)
        Account.AccountAddSegmentKey(builder, segment_key)
        Account.AccountAddEnvironment(builder, environment)
        Account.AccountAddBroker(builder, broker)
        Account.AccountAddStatus(builder, status)
        return Account.AccountEnd(builder)

    rows = (account("spot"), account("usd_m_futures"))
    Accounts.AccountsStartAccountsVector(builder, len(rows))
    for row in reversed(rows):
        builder.PrependUOffsetTRelative(row)
    row_vector = builder.EndVector()
    Accounts.AccountsStart(builder)
    Accounts.AccountsAddAccountCount(builder, len(rows))
    Accounts.AccountsAddActiveCount(builder, len(rows))
    Accounts.AccountsAddAccounts(builder, row_vector)
    payload = Accounts.AccountsEnd(builder)

    snapshot_id = builder.CreateString("account:7")
    view_key = builder.CreateString("account.current")
    owner = builder.CreateString("account-main")
    SnapshotHeader.SnapshotHeaderStart(builder)
    SnapshotHeader.SnapshotHeaderAddSnapshotId(builder, snapshot_id)
    SnapshotHeader.SnapshotHeaderAddViewKey(builder, view_key)
    SnapshotHeader.SnapshotHeaderAddOwnerActorId(builder, owner)
    SnapshotHeader.SnapshotHeaderAddGeneration(builder, 7)
    header = SnapshotHeader.SnapshotHeaderEnd(builder)

    AccountsSnapshot.AccountsSnapshotStart(builder)
    AccountsSnapshot.AccountsSnapshotAddHeader(builder, header)
    AccountsSnapshot.AccountsSnapshotAddPayload(builder, payload)
    root = AccountsSnapshot.AccountsSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"AAC1")
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

    snapshot = AccountMmapProjection(path).snapshot(AccountId("main"))

    assert snapshot.generation == 7
    assert [str(value.segment_key) for value in snapshot.segments] == [
        "spot",
        "usd_m_futures",
    ]
    assert {value.generation for value in snapshot.segments} == {7}
