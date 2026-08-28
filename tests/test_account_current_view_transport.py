from pathlib import Path

from kairospy.contracts.account import AccountCurrentView


def test_account_indexed_path_is_owner_and_account_scoped() -> None:
    assert AccountCurrentView(Path("/views"), "main", "workspace").path == Path(
        "/views/views/v3/Account/account-main/epoch-1/current.lmdb"
    )
