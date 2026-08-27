from pathlib import Path

from kairospy.infrastructure.contracts.account import indexed_environment_path


def test_account_indexed_path_is_owner_and_account_scoped() -> None:
    assert indexed_environment_path(
        Path("/views"), "main", "workspace"
    ) == Path("/views/views/v3/Account/account-main/epoch-1/current.lmdb")
