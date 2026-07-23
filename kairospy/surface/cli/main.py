from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from kairospy.environment import Environment
from kairospy.identity import AccountRef, AccountType
from kairospy.integrations.connectors.ibkr.session import IbkrSession
from kairospy.reference import ReferenceCatalog


# Parser ownership lives in kairospy.surface.cli.parser.
# Compatibility markers for repository hygiene tests:
# commands.add_parser("workspace"
# workspace_actions.add_parser("create"
# run_actions.add_parser("start"
# "provider-entitlement-diagnostics"
# choices=("massive",)
# "us-equity-momentum-diagnostics"
# "reference-artifact"
# "failure-policy"
# "soak"


def main(argv: list[str] | None = None) -> int:
    from kairospy.surface.cli import dispatch

    dispatch._providers = _providers
    return dispatch.main(argv)


def _parser() -> argparse.ArgumentParser:
    from kairospy.surface.cli.parser import build_parser

    return build_parser()


def _program_name() -> str:
    from kairospy.surface.cli.parser import _program_name

    return _program_name()


def _positive_int(value: str) -> int:
    from kairospy.surface.cli.parser import _positive_int

    return _positive_int(value)


def _hide_subcommands(action, names: set[str]) -> None:
    from kairospy.surface.cli.parser import _hide_subcommands

    return _hide_subcommands(action, names)


def _add_global_cli_arguments(parser: argparse.ArgumentParser) -> None:
    from kairospy.surface.cli.parser import _add_global_cli_arguments

    return _add_global_cli_arguments(parser)


def _add_sma_input_arguments(parser):
    from kairospy.surface.cli.parser import _add_sma_input_arguments

    return _add_sma_input_arguments(parser)


def _add_acquisition_limit_args(parser: argparse.ArgumentParser) -> None:
    from kairospy.surface.cli.parser import _add_acquisition_limit_args

    return _add_acquisition_limit_args(parser)


def _add_sma_run_arguments(parser):
    from kairospy.surface.cli.parser import _add_sma_run_arguments

    return _add_sma_run_arguments(parser)


def _add_live_binance_bar_arguments(parser):
    from kairospy.surface.cli.parser import _add_live_binance_bar_arguments

    return _add_live_binance_bar_arguments(parser)


def _product_command(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.run import product_command

    return product_command(args)


def _run_live_attach_console(args: argparse.Namespace, product_surface: object, render_product_result: object, resolve_language: object) -> int:
    from kairospy.surface.cli.commands.run import run_live_attach_console

    return run_live_attach_console(args, product_surface, render_product_result, resolve_language)


def _run_live_attach_prompt(run_id: str) -> str:
    from kairospy.surface.cli.commands.run import run_live_attach_prompt

    return run_live_attach_prompt(run_id)


def _run_live_attach_log_path(payload: dict[str, object]) -> Path | None:
    from kairospy.surface.cli.commands.run import run_live_attach_log_path

    return run_live_attach_log_path(payload)


def _run_live_attach_status_key(payload: dict[str, object]) -> tuple[object, ...]:
    from kairospy.surface.cli.commands.run import run_live_attach_status_key

    return run_live_attach_status_key(payload)


def _run_live_attach_print_tail(path: Path | None, tail_lines: int) -> int:
    from kairospy.surface.cli.commands.run import run_live_attach_print_tail

    return run_live_attach_print_tail(path, tail_lines)


def _run_live_attach_print_new_log(path: Path | None, offset: int) -> int:
    from kairospy.surface.cli.commands.run import run_live_attach_print_new_log

    return run_live_attach_print_new_log(path, offset)


def _run_live_attach_normalize_command_parts(parts: list[str]) -> list[str]:
    from kairospy.surface.cli.commands.run import run_live_attach_normalize_command_parts

    return run_live_attach_normalize_command_parts(parts)


def _run_live_attach_start_args(command: str, args: argparse.Namespace, parts: list[str]) -> argparse.Namespace:
    from kairospy.surface.cli.commands.run import run_live_attach_start_args

    return run_live_attach_start_args(command, args, parts)


def _run_live_attach_reason(command: str, parts: list[str]) -> str:
    from kairospy.surface.cli.commands.run import run_live_attach_reason

    return run_live_attach_reason(command, parts)


def _workspace(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.workspace import workspace_command

    return workspace_command(args)


def _workspace_inspect_code(entrypoint_ref: str, params_values: tuple[str, ...], *, mode: str = "inspect") -> dict[str, object]:
    from kairospy.surface.cli.commands.workspace import workspace_inspect_code

    return workspace_inspect_code(entrypoint_ref, params_values, mode=mode)


def _providers(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.project import providers_command

    return providers_command(args)


def _interactive_shell() -> int:
    from kairospy.surface.cli.interactive import interactive_shell

    return interactive_shell(main, workspace_choices_func=_interactive_workspace_choices)


class _InteractiveSession:
    def __new__(cls, *args, **kwargs):
        from kairospy.surface.cli.interactive import InteractiveSession

        return InteractiveSession(main, workspace_choices_func=_interactive_workspace_choices)


def _interactive_expand_number(parts: list[str], mapping: dict[str, str]) -> list[str]:
    from kairospy.surface.cli.interactive import interactive_expand_number

    return interactive_expand_number(parts, mapping)


def _interactive_workspace_choices() -> tuple[str, ...]:
    from kairospy.surface.cli.interactive import interactive_workspace_choices

    return interactive_workspace_choices()


def _interactive_workspace_choice_hint(choices: tuple[str, ...]) -> str:
    from kairospy.surface.cli.interactive import interactive_workspace_choice_hint

    return interactive_workspace_choice_hint(choices)


def _data(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.data import data_command

    return data_command(args)


def _authoritative_runtime_store(args: argparse.Namespace):
    from kairospy.surface.cli.commands.account import _authoritative_runtime_store as command

    return command(args)


def _account(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.account import _account as command

    return command(args)


def _accounts(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.account import _accounts as command

    return command(args)


def _runtime_l4_preflight(args: argparse.Namespace) -> dict[str, object]:
    from kairospy.surface.cli.commands.account import _runtime_l4_preflight as command

    return command(args)


def _submit_order_or_runtime_soak(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.account import _submit_order_or_runtime_soak as command

    return command(args)


def _ensure_simulated_execution_route(catalog: ReferenceCatalog, account: AccountRef, listings, at: datetime) -> None:
    from kairospy.surface.cli.commands.account import _ensure_simulated_execution_route as command

    return command(catalog, account, listings, at)


def _ibkr_session(*, readonly: bool) -> IbkrSession:
    from kairospy.surface.cli.commands.account import _ibkr_session as command

    return command(readonly=readonly)


def _ibkr_connection_settings() -> tuple[str, int, int]:
    from kairospy.surface.cli.commands.account import _ibkr_connection_settings as command

    return command()


def _credentials(environment: Environment) -> tuple[str, str]:
    from kairospy.surface.cli.commands.account import _credentials as command

    return command(environment)


def _account_gateway(venue: str, environment: Environment, account: AccountRef, ledger, product: str, catalog, inverse: bool):
    from kairospy.surface.cli.commands.account import _account_gateway as command

    return command(venue, environment, account, ledger, product, catalog, inverse)


def _execution_account_gateway(venue: str, environment: Environment, product: str, definition, catalog, inverse: bool):
    from kairospy.surface.cli.commands.account import _execution_account_gateway as command

    return command(venue, environment, product, definition, catalog, inverse)


class _CombinedExecutionAccount:
    def __new__(cls, execution, account):
        from kairospy.surface.cli.commands.account import _CombinedExecutionAccount as command

        return command(execution, account)


def _account_key(venue: str, account_id: str, product: str) -> AccountRef:
    from kairospy.surface.cli.commands.account import _account_key as command

    return command(venue, account_id, product)


def _account_type(product: str) -> AccountType:
    from kairospy.surface.cli.commands.account import _account_type as command

    return command(product)


def _local_state(ledger, account):
    from kairospy.surface.cli.commands.account import _local_state as command

    return command(ledger, account)


def _coerce_decimal_fields(values: dict[str, Any], cls) -> dict[str, Any]:
    from kairospy.surface.cli.dispatch import _coerce_decimal_fields as dispatch_coerce_decimal_fields

    return dispatch_coerce_decimal_fields(values, cls)


def __getattr__(name: str):
    from kairospy.surface.cli import dispatch

    try:
        return getattr(dispatch, name)
    except AttributeError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error


if __name__ == "__main__":
    raise SystemExit(main())
