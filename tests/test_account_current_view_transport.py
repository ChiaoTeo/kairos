from pathlib import Path

import pytest

from kairospy.infrastructure.contracts.account.view_contract import (
    BALANCES_DATABASE,
    account_indexed_environment_path,
    account_indexed_key,
    account_indexed_key_parts,
    decode_indexed_value,
)


def test_account_indexed_path_is_owner_and_account_scoped() -> None:
    assert account_indexed_environment_path(Path("/views"), "main") == Path(
        "/views/views/v3/Account/account-main/epoch-1/current.lmdb"
    )


def test_account_indexed_composite_key_round_trips_without_ambiguity() -> None:
    key = account_indexed_key("spot", "USDT")
    assert account_indexed_key_parts(key) == ("spot", "USDT")
    assert key != account_indexed_key("spot:USDT")


def test_account_indexed_decoder_rejects_old_or_wrong_root() -> None:
    with pytest.raises(ValueError, match="identifier"):
        decode_indexed_value(b"\0\0\0\0AAV2", BALANCES_DATABASE)
