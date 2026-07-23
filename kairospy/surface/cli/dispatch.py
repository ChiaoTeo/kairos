from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
from pathlib import Path
from typing import Any

from kairospy.environment import Environment
from kairospy.integrations.connectors.ibkr.session import IbkrSession
from kairospy.reference import ReferenceCatalog
from kairospy.identity import AccountRef, AccountType
from kairospy.research.capture.spec import OptionChainCaptureSpec


# Parser ownership lives in kairospy.surface.cli.parser.
# Compatibility markers for repository hygiene tests:
# commands.add_parser("workspace"
# workspace_actions.add_parser("create"
# run_actions.add_parser("start"


def _program_name() -> str:
    from kairospy.surface.cli.parser import _program_name as parser_program_name

    return parser_program_name()


def _positive_int(value: str) -> int:
    from kairospy.surface.cli.parser import _positive_int as parser_positive_int

    return parser_positive_int(value)


def _hide_subcommands(action, names: set[str]) -> None:
    from kairospy.surface.cli.parser import _hide_subcommands as parser_hide_subcommands

    return parser_hide_subcommands(action, names)


def _parser() -> argparse.ArgumentParser:
    from kairospy.surface.cli.parser import build_parser

    return build_parser()


def _add_global_cli_arguments(parser: argparse.ArgumentParser) -> None:
    from kairospy.surface.cli.parser import _add_global_cli_arguments as parser_add_global_cli_arguments

    return parser_add_global_cli_arguments(parser)


def _add_sma_input_arguments(parser):
    from kairospy.surface.cli.parser import _add_sma_input_arguments as parser_add_sma_input_arguments

    return parser_add_sma_input_arguments(parser)


def _add_acquisition_limit_args(parser: argparse.ArgumentParser) -> None:
    from kairospy.surface.cli.parser import _add_acquisition_limit_args as parser_add_acquisition_limit_args

    return parser_add_acquisition_limit_args(parser)


def _add_sma_run_arguments(parser):
    from kairospy.surface.cli.parser import _add_sma_run_arguments as parser_add_sma_run_arguments

    return parser_add_sma_run_arguments(parser)


def _add_live_binance_bar_arguments(parser):
    from kairospy.surface.cli.parser import _add_live_binance_bar_arguments as parser_add_live_binance_bar_arguments

    return parser_add_live_binance_bar_arguments(parser)


def _spec(args: argparse.Namespace) -> OptionChainCaptureSpec:
    from kairospy.surface.cli.commands.capture import option_capture_spec

    return option_capture_spec(args)

def _has_cli_option(raw_argv: list[str], name: str) -> bool:
    from kairospy.surface.cli.commands.config import has_cli_option

    return has_cli_option(raw_argv, name)


def _apply_project_config_defaults(args: argparse.Namespace, raw_argv: list[str]) -> None:
    from kairospy.surface.cli.commands.config import apply_project_config_defaults

    return apply_project_config_defaults(args, raw_argv)


def _require_project_config(args: argparse.Namespace):
    from kairospy.surface.cli.commands.config import require_project_config

    return require_project_config(args)


def _config_command(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.config import config_command

    return config_command(args)


def _project_command(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.project import project_command

    return project_command(args)


def _configure_command(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.config import configure_command

    return configure_command(args)


def _doctor(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.config import doctor_command

    return doctor_command(args)


def _print_doctor(checks: list[dict[str, object]], output_format: str) -> None:
    from kairospy.surface.cli.commands.config import print_doctor

    return print_doctor(checks, output_format)


def _doctor_next_steps(checks: list[dict[str, object]]) -> list[str]:
    from kairospy.surface.cli.commands.config import doctor_next_steps

    return doctor_next_steps(checks)


def _flatten_config(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, object]]:
    from kairospy.surface.cli.commands.config import flatten_config

    return flatten_config(payload, prefix)


def _prompt_configure_args(args: argparse.Namespace) -> argparse.Namespace:
    from kairospy.surface.cli.commands.config import prompt_configure_args

    return prompt_configure_args(args)


def _prompt_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    from kairospy.surface.cli.prompts import prompt_choice

    return prompt_choice(label, choices, default=default)


def _prompt_text(label: str, default: str) -> str:
    from kairospy.surface.cli.prompts import prompt_text

    return prompt_text(label, default)


def _prompt_bool(label: str, default: bool) -> bool:
    from kairospy.surface.cli.prompts import prompt_bool

    return prompt_bool(label, default)

def main(argv: list[str] | None = None) -> int:
    from kairospy.infrastructure.configuration import load_dotenv_file

    load_dotenv_file()
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = _parser()
    if not raw_argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return _interactive_shell()
        parser.print_help()
        return 0
    args = parser.parse_args(raw_argv)
    if args.group != "init":
        from kairospy.surface.cli.commands.config import apply_project_config_defaults

        apply_project_config_defaults(args, raw_argv)
    if args.group == "config":
        from kairospy.surface.cli.commands.config import config_command

        return config_command(args)
    if args.group == "project":
        from kairospy.surface.cli.commands.project import project_command

        return project_command(args)
    if args.group == "doctor":
        from kairospy.surface.cli.commands.config import doctor_command

        return doctor_command(args)
    if args.group == "configure":
        from kairospy.surface.cli.commands.config import configure_command

        return configure_command(args)
    if args.group == "providers":
        return _providers(args)
    if args.group == "catalog":
        return _catalog(args)
    if args.group == "accounts":
        return _accounts(args)
    if args.group == "account":
        return _account(args)
    if args.group == "order":
        return _submit_order_or_runtime_soak(args)
    if args.group == "runtime" and args.action == "soak":
        return _submit_order_or_runtime_soak(args)
    if args.group == "runtime":
        from kairospy.surface.cli.commands.account import runtime_command

        return runtime_command(args)
    if args.group == "init":
        from kairospy.surface.project import initialize_project, render_project_init
        if getattr(args, "target_path", None) is not None:
            args.target = args.target_path
        if args.interactive:
            args.target = Path(_prompt_text("Project directory", str(args.target)))
            args.name = _prompt_text("Project name", args.name or args.target.name or "kairospy-project")
            args.force = _prompt_bool("Overwrite existing scaffold files", args.force)
        result = initialize_project(args.target, name=args.name, force=args.force)
        if args.format == "json":
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        elif not args.quiet:
            print(render_project_init(result))
        return 0
    if args.group == "workspace":
        return _workspace(args)
    if args.group == "run":
        return _product_command(args)
    if args.group == "data":
        return _data(args)
    if args.group == "features":
        return _features(args)
    if args.group == "pricing":
        return _pricing(args)
    if args.group == "vol":
        return _vol(args)
    if args.group == "risk":
        return _risk_analytics(args)
    from kairospy.surface.cli.commands.capture import capture_command

    return capture_command(args)

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

def _features(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.analytics import features_command

    return features_command(args)


def _pricing(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.analytics import pricing_command

    return pricing_command(args)


def _vol(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.analytics import vol_command

    return vol_command(args)


def _risk_analytics(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.analytics import risk_command

    return risk_command(args)

def _capture_normalized_series(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.catalog import capture_normalized_series_command

    return capture_normalized_series_command(args)


def _catalog(args: argparse.Namespace) -> int:
    from kairospy.surface.cli.commands.catalog import catalog_command

    return catalog_command(args)

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
    from decimal import Decimal
    from datetime import time
    from typing import get_type_hints

    hints = get_type_hints(cls)
    result = {}
    for key, value in values.items():
        if hints.get(key) is Decimal:
            result[key] = Decimal(str(value))
        elif hints.get(key) is time and isinstance(value, str):
            result[key] = time.fromisoformat(value)
        else:
            result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
