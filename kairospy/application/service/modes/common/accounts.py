from __future__ import annotations

from typing import Mapping
from typing import TypeVar

from kairospy.application.system.accounts import AccountRegistry, SystemAccount

from .config import account_selector, optional_int, optional_text

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


def configured_account(
    accounts: object,
    *,
    venue: str,
    mode_config: Mapping[str, object],
    mode_label: str,
    error_type: type[ConfigErrorT],
    require_accounts_table: bool = True,
) -> SystemAccount:
    registry = AccountRegistry.from_config(accounts)  # type: ignore[arg-type]
    if require_accounts_table and not registry.accounts:
        raise error_type(f"[accounts] table is required for {mode_label} runs")
    try:
        return registry.resolve(
            venue=venue,
            account=account_selector(mode_config.get("account"), f"{mode_label}.account", error_type),
            account_id=optional_text(mode_config.get("account_id"), f"{mode_label}.account_id", error_type),
            account_index=optional_int(mode_config.get("account_index"), f"{mode_label}.account_index", error_type),
        )
    except ValueError as error:
        raise error_type(str(error)) from error


__all__ = ["configured_account"]
